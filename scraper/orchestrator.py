"""
スクレイピングオーケストレーター
① HP/求人スクレイプ → ② SNS深掘り（実ページ訪問）→ ③ Claude SNS診断 → ④ 再スコアリング
"""
import logging
import time
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import Lead, ScrapeJob, ScrapeStatus, LeadStatus
from scraper.company_scraper import scrape_and_update_lead
from scraper.job_scraper import estimate_budget_from_jobs
from lead_generator.scorer import score_lead
from config import settings

logger = logging.getLogger(__name__)


def run_scrape_for_lead(
    lead: Lead,
    db: Session,
    run_sns_deep: bool = True,
    run_sns_diagnosis: bool = True,
) -> dict:
    """
    1社分のフルスクレイピングを実行してDBを更新する

    Args:
        run_sns_deep:      各SNSページに実際にアクセスしてデータ収集するか
        run_sns_diagnosis: Claude APIでSNS診断レポートを生成するか

    Returns: {"success": bool, "changes": dict, "errors": list}
    """
    changes = {}
    errors = []

    # ① 企業HPスクレイピング（SNSリンク検出・電話番号・広告タグ）
    if lead.website:
        try:
            hp_data = scrape_and_update_lead(lead)
            changes["website"] = hp_data
        except Exception as e:
            errors.append(f"HP scrape: {e}")

    time.sleep(settings.scrape_delay_seconds)

    # ② 求人サイトスクレイピング（予算規模・マーケター採用状況）
    try:
        job_data = estimate_budget_from_jobs(lead.company_name, lead.prefecture or "")
        if job_data["budget_estimate"] != "不明":
            lead.budget_estimate = job_data["budget_estimate"]
            changes["budget"] = job_data["budget_estimate"]
        if job_data["is_hiring_marketer"]:
            lead.is_hiring_marketer = True
            changes["is_hiring_marketer"] = True
    except Exception as e:
        errors.append(f"job scrape: {e}")

    time.sleep(settings.scrape_delay_seconds)

    # ③ SNS深掘りスクレイピング（実際のSNSページに訪問）
    if run_sns_deep and _has_any_sns(lead):
        try:
            from scraper.sns_deep_scraper import deep_scrape_all_sns
            sns_raw = deep_scrape_all_sns(lead)
            lead.sns_raw_data = sns_raw
            changes["sns_raw"] = {
                "platforms": list(k for k in sns_raw if k != "summary"),
                "summary": sns_raw.get("summary", {}),
            }

            # Instagram フォロワーをLeadに反映
            ig = sns_raw.get("instagram", {})
            if ig and ig.get("followers") and not lead.sns_follower_estimate:
                lead.sns_follower_estimate = _format_follower(ig["followers"])
            if ig and ig.get("post_frequency_hint"):
                lead.sns_post_frequency = lead.sns_post_frequency or ig["post_frequency_hint"]

        except Exception as e:
            errors.append(f"SNS deep scrape: {e}")
            logger.error(f"SNS deep scrape error for {lead.company_name}: {e}")

        time.sleep(settings.scrape_delay_seconds)

    # ④ Claude SNS診断レポート生成
    if run_sns_diagnosis and lead.sns_raw_data:
        try:
            from scraper.sns_proposal_generator import generate_sns_diagnosis
            diagnosis = generate_sns_diagnosis(
                lead.company_name,
                lead.industry or "不明",
                lead.sns_raw_data,
            )
            lead.sns_diagnosis = diagnosis
            lead.sns_analyzed_at = datetime.utcnow()
            changes["sns_diagnosis"] = {
                "overall_score": diagnosis.get("overall_score"),
                "potential_score": diagnosis.get("potential_score"),
                "issues_count": len(diagnosis.get("critical_issues", [])),
                "proposals_count": len(diagnosis.get("improvement_proposals", [])),
            }
        except Exception as e:
            errors.append(f"SNS diagnosis: {e}")
            logger.error(f"SNS diagnosis error for {lead.company_name}: {e}")

    # ⑤ 再スコアリング（SNS診断結果を加味）
    new_score, new_service = score_lead(lead)
    changes["score_before"] = lead.lead_score
    changes["score_after"] = new_score
    lead.lead_score = new_score
    lead.recommended_service = new_service
    lead.scrape_status = ScrapeStatus.DONE if not errors else ScrapeStatus.FAILED
    lead.scraped_at = datetime.utcnow()

    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.RESEARCHED

    # ScrapeJob ログ
    job = ScrapeJob(
        lead_id=lead.id,
        target_url=lead.website or "",
        scrape_type="full_with_sns",
        status=ScrapeStatus.DONE if not errors else ScrapeStatus.FAILED,
        result_data=changes,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        error="; ".join(errors) if errors else None,
    )
    db.add(job)
    db.flush()

    return {"success": not errors, "changes": changes, "errors": errors}


def run_sns_analysis_only(lead: Lead, db: Session) -> dict:
    """
    既にHP情報が揃っているリードに対してSNS深掘り分析だけを実行する
    （スクレイピング済みだが診断がまだの場合に使用）
    """
    errors = []

    if not _has_any_sns(lead):
        return {"success": False, "errors": ["SNSアカウントなし"]}

    # SNS深掘りスクレイピング
    try:
        from scraper.sns_deep_scraper import deep_scrape_all_sns
        sns_raw = deep_scrape_all_sns(lead)
        lead.sns_raw_data = sns_raw
    except Exception as e:
        errors.append(f"SNS scrape: {e}")
        return {"success": False, "errors": errors}

    # Claude診断
    try:
        from scraper.sns_proposal_generator import generate_sns_diagnosis
        lead.sns_diagnosis = generate_sns_diagnosis(
            lead.company_name, lead.industry or "不明", sns_raw
        )
        lead.sns_analyzed_at = datetime.utcnow()
    except Exception as e:
        errors.append(f"diagnosis: {e}")

    db.flush()
    return {
        "success": True,
        "sns_score": lead.sns_diagnosis.get("overall_score") if lead.sns_diagnosis else None,
        "errors": errors,
    }


def run_batch_scrape(
    db: Session,
    limit: int = 20,
    only_new: bool = True,
    run_sns_deep: bool = True,
    run_sns_diagnosis: bool = True,
) -> dict:
    """未スクレイピングのリードを一括処理する"""
    q = db.query(Lead)
    if only_new:
        q = q.filter(Lead.scrape_status == ScrapeStatus.PENDING)
    q = q.filter(Lead.website.isnot(None)).limit(limit)
    leads = q.all()

    results = {"total": len(leads), "success": 0, "failed": 0}
    for lead in leads:
        logger.info(f"Scraping [{lead.company_name}] ...")
        r = run_scrape_for_lead(lead, db, run_sns_deep, run_sns_diagnosis)
        if r["success"]:
            results["success"] += 1
        else:
            results["failed"] += 1
        db.flush()
        time.sleep(settings.scrape_delay_seconds)

    db.commit()
    return results


def _has_any_sns(lead: Lead) -> bool:
    return bool(
        lead.instagram_url or lead.tiktok_url or lead.youtube_url or lead.twitter_url or
        lead.has_instagram or lead.has_tiktok or lead.has_youtube or lead.has_twitter
    )


def _format_follower(n: int) -> str:
    if n >= 100000:
        return f"{n // 10000}万以上"
    elif n >= 10000:
        return f"{n // 1000}千〜{(n // 1000) + 1}千"
    elif n >= 1000:
        return f"{n // 1000}千"
    elif n >= 100:
        return f"{n // 100}百"
    return f"{n}未満"
