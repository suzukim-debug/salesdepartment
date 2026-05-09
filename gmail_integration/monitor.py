"""
Gmail 返信・開封監視
- 送信済みスレッドへの返信を検知 → ステータスをREPLIED/RESPONDEDに更新
- Gmail の Labels API で開封を補助的に確認
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import Lead, OutreachLog, TrackingEvent, LeadStatus
from config import settings

logger = logging.getLogger(__name__)


def _get_thread_messages(service, thread_id: str) -> list:
    """スレッド内のメッセージ一覧を取得"""
    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
        return thread.get("messages", [])
    except Exception as e:
        logger.warning(f"thread fetch failed {thread_id}: {e}")
        return []


def _is_reply_from_recipient(message: dict, original_sender: str) -> bool:
    """このメッセージが相手からの返信かどうか判定"""
    headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
    from_header = headers.get("from", "")
    return original_sender.lower() in from_header.lower()


def check_gmail_replies(db: Session, service) -> dict:
    """
    送信済みアウトリーチのスレッドを確認して返信を検知する
    スケジューラから定期的に呼ばれる

    Returns: {"checked": int, "new_replies": int}
    """
    checked = 0
    new_replies = 0

    # 送信済み・未返信のログを対象に
    logs = (
        db.query(OutreachLog)
        .filter(
            OutreachLog.gmail_thread_id.isnot(None),
            OutreachLog.sent_at.isnot(None),
            OutreachLog.replied_at.is_(None),
        )
        .all()
    )

    for log in logs:
        lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
        if not lead or not lead.contact_email:
            continue

        checked += 1
        messages = _get_thread_messages(service, log.gmail_thread_id)

        # スレッドに2件以上メッセージがある = 返信あり
        if len(messages) > 1:
            for msg in messages[1:]:  # 最初のメッセージ（自分の送信）は除外
                if _is_reply_from_recipient(msg, lead.contact_email):
                    log.replied_at = datetime.utcnow()
                    new_replies += 1

                    # リードステータスを更新
                    _update_status_on_reply(db, lead, log)
                    break

    db.commit()
    return {"checked": checked, "new_replies": new_replies}


def _update_status_on_reply(db: Session, lead: Lead, log: OutreachLog) -> None:
    STATUS_RANK = {
        LeadStatus.NEW: 0, LeadStatus.RESEARCHED: 1,
        LeadStatus.EMAIL_SENT: 2, LeadStatus.OPENED: 3,
        LeadStatus.CLICKED: 4, LeadStatus.REPLIED: 5,
        LeadStatus.RESPONDED: 6,
    }
    current_rank = STATUS_RANK.get(lead.status, 0)
    reply_rank = STATUS_RANK[LeadStatus.REPLIED]

    if current_rank < reply_rank and lead.status not in (
        LeadStatus.REJECTED, LeadStatus.UNSUBSCRIBED,
        LeadStatus.MEETING_SET, LeadStatus.CALLING, LeadStatus.CONNECTED,
    ):
        lead.status = LeadStatus.REPLIED

    # TrackingEvent に記録
    event = TrackingEvent(
        lead_id=lead.id,
        outreach_log_id=log.id,
        event_type="reply",
        event_data=f'{{"thread_id": "{log.gmail_thread_id}"}}',
    )
    db.add(event)
    logger.info(f"返信検知: {lead.company_name} (thread: {log.gmail_thread_id})")


def check_gmail_sends_status(db: Session, service) -> dict:
    """
    Gmailのラベルから「送信済み」ステータスを確認して補完する
    （smtpフォールバック時などgmail_message_idがないケースの補完）
    """
    try:
        # 直近100件の送信済みメッセージを取得
        result = service.users().messages().list(
            userId="me",
            labelIds=["SENT"],
            maxResults=100,
        ).execute()
        messages = result.get("messages", [])
        return {"sent_count_in_gmail": len(messages)}
    except Exception as e:
        logger.error(f"Gmail status check failed: {e}")
        return {"error": str(e)}
