"""
Gmail API メール送信
- スケジュール送信（指定日時まで待機してから送信）
- 開封トラッキングピクセル埋め込み
- スレッドIDの記録
"""
import base64
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session
from database.models import Lead, OutreachLog, LeadStatus, ServiceType
from outreach.email_sender import create_outreach_log
from config import settings

logger = logging.getLogger(__name__)


def _build_mime_message(
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    from_name: str = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_addr = settings.gmail_sender_email or settings.smtp_user
    msg["From"] = f"{from_name or settings.gmail_from_name} <{from_addr}>"
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def _encode_message(msg: MIMEMultipart) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def send_via_gmail(
    service,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    thread_id: Optional[str] = None,
) -> dict:
    """
    Gmail APIでメール送信する
    Returns: {"message_id": str, "thread_id": str}
    """
    msg = _build_mime_message(to_email, subject, body_text, body_html)
    encoded = _encode_message(msg)

    body = {"raw": encoded}
    if thread_id:
        body["threadId"] = thread_id

    result = service.users().messages().send(
        userId="me", body=body
    ).execute()

    return {
        "message_id": result.get("id"),
        "thread_id": result.get("threadId"),
    }


def send_outreach_via_gmail(
    db: Session,
    lead: Lead,
    subject: str,
    body_text: str,
    body_html: str,
    service_type: ServiceType,
    scheduled_at: Optional[datetime] = None,
) -> OutreachLog:
    """
    Gmail APIでアウトリーチメールを送信してOutreachLogに記録する
    scheduled_at が指定された場合はスケジューラに登録して即時返却する
    """
    if not lead.contact_email:
        raise ValueError(f"メールアドレスがありません: {lead.company_name}")

    log = create_outreach_log(db, lead, subject, body_text, body_html, service_type)
    log.scheduled_at = scheduled_at

    # スケジュール送信の場合はここで終了（schedulerが後で送信）
    if scheduled_at and scheduled_at > datetime.utcnow():
        db.flush()
        return log

    try:
        from gmail_integration.auth import get_gmail_service
        service = get_gmail_service()
        result = send_via_gmail(service, lead.contact_email, subject, body_text, log.body_html)

        log.gmail_message_id = result["message_id"]
        log.gmail_thread_id = result["thread_id"]
        log.sent_at = datetime.utcnow()
        lead.status = LeadStatus.EMAIL_SENT

        # スレッドIDをLeadにも保存
        if lead.gmail_thread_ids is None:
            lead.gmail_thread_ids = []
        lead.gmail_thread_ids = lead.gmail_thread_ids + [result["thread_id"]]

    except HttpError as e:
        log.error = f"Gmail API error: {e}"
        logger.error(f"Send failed for {lead.company_name}: {e}")
        raise
    except FileNotFoundError:
        # Gmail未設定の場合は SMTP にフォールバック
        logger.warning("Gmail未設定: SMTPにフォールバック")
        from outreach.email_sender import send_outreach_email
        return send_outreach_email(db, lead, subject, body_text, body_html, service_type)

    db.flush()
    return log


def send_scheduled_emails(db: Session) -> dict:
    """
    scheduled_at が現在時刻を過ぎた未送信メールを送信する（スケジューラから呼ばれる）
    """
    from database.models import OutreachLog
    now = datetime.utcnow()

    pending = (
        db.query(OutreachLog)
        .filter(
            OutreachLog.scheduled_at <= now,
            OutreachLog.sent_at.is_(None),
            OutreachLog.error.is_(None),
        )
        .all()
    )

    sent = 0
    failed = 0

    for log in pending:
        lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
        if not lead or not lead.contact_email:
            continue
        try:
            from gmail_integration.auth import get_gmail_service
            service = get_gmail_service()
            result = send_via_gmail(
                service, lead.contact_email,
                log.subject, log.body_text, log.body_html,
            )
            log.gmail_message_id = result["message_id"]
            log.gmail_thread_id = result["thread_id"]
            log.sent_at = now
            lead.status = LeadStatus.EMAIL_SENT
            sent += 1
        except Exception as e:
            log.error = str(e)
            failed += 1

    db.commit()
    return {"sent": sent, "failed": failed}
