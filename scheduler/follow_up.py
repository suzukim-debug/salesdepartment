"""
フォローアップスケジューラ
- 7日後フォローアップメール自動送信
- Gmail返信チェック（15分間隔）
- Sheetsへの自動エクスポート（毎朝）
- スケジュール済みメールの送信
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database.models import (
    Lead, OutreachLog, FollowUpLog, LeadStatus, ServiceType, SchedulerJob
)
from outreach.template_generator import generate_email
from config import settings

logger = logging.getLogger(__name__)

MAX_FOLLOW_UPS = 2  # フォローアップは最大2回まで


def _get_or_create_scheduler_job(db: Session, job_name: str) -> SchedulerJob:
    job = db.query(SchedulerJob).filter(SchedulerJob.job_name == job_name).first()
    if not job:
        job = SchedulerJob(job_name=job_name)
        db.add(job)
        db.flush()
    return job


def check_and_send_followups(db: Session) -> dict:
    """
    7日間反響なしのリードにフォローアップメールを送信する（⑦フォローアップ）
    毎朝9時にスケジューラから呼ばれる想定

    Returns: {"sent": int, "skipped": int}
    """
    sent_count = 0
    skipped_count = 0
    cutoff = datetime.utcnow() - timedelta(days=settings.follow_up_days)

    # フォローアップ対象: 初回メール送信後7日以上 & 反響なし & 最大フォローアップ未達
    candidates = (
        db.query(Lead)
        .filter(
            Lead.status.in_([LeadStatus.EMAIL_SENT, LeadStatus.OPENED]),
        )
        .all()
    )

    for lead in candidates:
        # 最新の初回メールを取得
        first_log = (
            db.query(OutreachLog)
            .filter(
                OutreachLog.lead_id == lead.id,
                OutreachLog.is_followup == False,
                OutreachLog.sent_at.isnot(None),
                OutreachLog.sent_at <= cutoff,
            )
            .order_by(OutreachLog.sent_at)
            .first()
        )

        if not first_log:
            continue

        # フォローアップ回数チェック
        followup_count = db.query(FollowUpLog).filter(
            FollowUpLog.lead_id == lead.id,
            FollowUpLog.result == "sent",
        ).count()

        if followup_count >= MAX_FOLLOW_UPS:
            skipped_count += 1
            continue

        # 既にフォローアップ送信済みの場合は最後のフォローから7日待つ
        last_followup = (
            db.query(FollowUpLog)
            .filter(FollowUpLog.lead_id == lead.id, FollowUpLog.result == "sent")
            .order_by(desc(FollowUpLog.sent_at))
            .first()
        )
        if last_followup and last_followup.sent_at > cutoff:
            skipped_count += 1
            continue

        # フォローアップメール生成・送信
        try:
            result = _send_followup(db, lead, first_log, followup_count + 1)
            if result:
                sent_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            logger.error(f"フォローアップ失敗 {lead.company_name}: {e}")
            _log_followup(db, lead, first_log, followup_count + 1, "error", str(e))

    db.commit()
    job = _get_or_create_scheduler_job(db, "follow_up")
    job.last_run_at = datetime.utcnow()
    job.last_result = f"sent:{sent_count}, skipped:{skipped_count}"
    db.commit()

    return {"sent": sent_count, "skipped": skipped_count}


def _send_followup(
    db: Session, lead: Lead, original_log: OutreachLog, follow_up_number: int
) -> bool:
    """フォローアップメールを1件送信する"""
    if not lead.contact_email:
        _log_followup(db, lead, original_log, follow_up_number, "skipped", "メールアドレスなし")
        return False

    try:
        # フォローアップ用プロンプトで生成
        email_data = _generate_followup_email(lead, original_log, follow_up_number)

        from gmail_integration.sender import send_outreach_via_gmail
        log = send_outreach_via_gmail(
            db, lead,
            email_data["subject"],
            email_data["body"],
            email_data["body_html"],
            ServiceType(email_data["service_type"]),
        )
        log.is_followup = True
        log.sequence_number = follow_up_number + 1

        lead.status = LeadStatus.FOLLOWUP_SENT
        _log_followup(db, lead, original_log, follow_up_number, "sent")
        db.flush()
        return True

    except Exception as e:
        _log_followup(db, lead, original_log, follow_up_number, "error", str(e))
        raise


def _generate_followup_email(lead: Lead, original_log: OutreachLog, number: int) -> dict:
    """フォローアップ用メールをClaudeで生成する"""
    import anthropic
    import json
    from config import settings

    days = settings.follow_up_days * number
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    prompt = f"""先日送ったメールの{number}回目フォローアップメールを書いてください。

【宛先企業】{lead.company_name}（{lead.industry or "不明"}）
【初回メール件名】{original_log.subject}
【フォローアップ回数】{number}回目
【経過日数】約{days}日

条件:
- 押し付けがましくない、軽いトーンで
- 「先日ご連絡しました〜」から始める
- 新しい価値提案（事例・数字）を1つ追加する
- 300文字以内の短いメール
- CTAは「お時間5分だけ」という低ハードルな表現にする

JSON形式で出力:
{{"subject": "件名", "body": "本文（プレーンテキスト）", "body_html": "本文（HTML）"}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    result["service_type"] = lead.recommended_service.value if lead.recommended_service else "mixed"
    return result


def _log_followup(
    db: Session, lead: Lead, original_log: OutreachLog,
    number: int, result: str, reason: str = ""
) -> None:
    log = FollowUpLog(
        lead_id=lead.id,
        outreach_log_id=original_log.id,
        follow_up_number=number,
        scheduled_at=datetime.utcnow(),
        sent_at=datetime.utcnow() if result == "sent" else None,
        result=result,
        skip_reason=reason,
    )
    db.add(log)
    db.flush()


def run_gmail_reply_check(db: Session) -> dict:
    """Gmailの返信チェック（15分間隔で実行）"""
    from gmail_integration.auth import is_gmail_configured, get_gmail_service
    from gmail_integration.monitor import check_gmail_replies

    if not is_gmail_configured():
        return {"skipped": True, "reason": "Gmail未設定"}

    try:
        service = get_gmail_service()
        result = check_gmail_replies(db, service)
        job = _get_or_create_scheduler_job(db, "gmail_reply_check")
        job.last_run_at = datetime.utcnow()
        job.last_result = f"checked:{result['checked']}, replies:{result['new_replies']}"
        db.commit()
        return result
    except Exception as e:
        logger.error(f"Gmail返信チェック失敗: {e}")
        return {"error": str(e)}


def run_sheets_export(db: Session) -> dict:
    """Sheetsエクスポート（毎朝実行）"""
    from sheets_integration.client import is_sheets_configured
    from sheets_integration.exporter import (
        export_hot_leads_to_sheets, sync_call_logs_to_sheets, write_analytics_to_sheets
    )

    if not is_sheets_configured():
        return {"skipped": True, "reason": "Sheets未設定"}

    try:
        r1 = export_hot_leads_to_sheets(db)
        r2 = sync_call_logs_to_sheets(db)
        r3 = write_analytics_to_sheets(db)

        job = _get_or_create_scheduler_job(db, "sheets_export")
        job.last_run_at = datetime.utcnow()
        job.last_result = f"leads:{r1['rows_written']}, calls:{r2['rows_written']}"
        db.commit()
        return {**r1, **r2, "analytics": "done"}
    except Exception as e:
        logger.error(f"Sheetsエクスポート失敗: {e}")
        return {"error": str(e)}
