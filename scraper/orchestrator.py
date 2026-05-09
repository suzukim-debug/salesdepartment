"""
スクレイピングオーケストレーター
スクレイプ → AIスコアリング → DB保存 を一気通貫で実行する
"""
import logging
import time
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import Lead, ScrapeJob, ScrapeStatus, LeadStatus
from scraper.company_scraper import scrape_and_update_lead
from scraper.job_scraper import estimate_budget_from_jobs
from scraper.sns_analyzer import analyze_sns_activity
from lead_generator.scorer import score_lead, batch_score_leads
from config import settings

logger = logging.getLogger(__name__)


def run_scrape_for_lead(lead: Lead, db: Session) -> dict:
    """
    1社分のスクレイピングを実行してDBを更新する
    Returns: {"success": bool, "changes": dict}
    """
    changes = {}
    errors = []

    # ① HP スクレイピング
    if lead.website:
        try:
            hp_data = scrape_and_update_lead(lead)
            changes["website"] = hp_data
        except Exception as e:
            errors.append(f"HP scrape: {e}")

    time.sleep(settings.scrape_delay_seconds)

    # ② 求人サイトスクレイピング
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

    # ③ SNSアクティビティ分析
    try:
        sns_data = analyze_sns_activity(lead)
        if sns_data["sns_follower_estimate"] != "不明":
            lead.sns_follower_estimate = sns_data["sns_follower_estimate"]
        changes["sns"] = sns_data
    except Exception as e:
        errors.append(f"SNS analyze: {e}")

    # ④ 再スコアリング
    new_score, new_service = score_lead(lead)
    changes["score_before"] = lead.lead_score
    changes["score_after"] = new_score
    lead.lead_score = new_score
    lead.recommended_service = new_service
    lead.scrape_status = ScrapeStatus.DONE if not errors else ScrapeStatus.FAILED
    lead.scraped_at = datetime.utcnow()

    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.RESEARCHED

    # ScrapeJob に記録
    job = ScrapeJob(
        lead_id=lead.id,
        target_url=lead.website or "",
        scrape_type="full",
        status=ScrapeStatus.DONE if not errors else ScrapeStatus.FAILED,
        result_data=changes,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        error="; ".join(errors) if errors else None,
    )
    db.add(job)
    db.flush()

    return {"success": not errors, "changes": changes, "errors": errors}


def run_batch_scrape(db: Session, limit: int = 20, only_new: bool = True) -> dict:
    """
    未スクレイピングのリードを一括処理する
    """
    q = db.query(Lead)
    if only_new:
        q = q.filter(Lead.scrape_status == ScrapeStatus.PENDING)
    q = q.filter(Lead.website.isnot(None)).limit(limit)
    leads = q.all()

    results = {"total": len(leads), "success": 0, "failed": 0}
    for lead in leads:
        logger.info(f"Scraping: {lead.company_name}")
        r = run_scrape_for_lead(lead, db)
        if r["success"]:
            results["success"] += 1
        else:
            results["failed"] += 1
        db.flush()
        time.sleep(settings.scrape_delay_seconds)

    db.commit()
    return results
