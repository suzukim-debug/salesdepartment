"""
メール開封・クリック・フォーム回答を記録する
FastAPIのエンドポイントから呼ばれる
"""
import json
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import Lead, OutreachLog, TrackingEvent, LeadStatus


def _update_lead_status_if_higher(lead: Lead, new_status: LeadStatus) -> None:
    STATUS_RANK = {
        LeadStatus.NEW: 0,
        LeadStatus.RESEARCHED: 1,
        LeadStatus.EMAIL_SENT: 2,
        LeadStatus.OPENED: 3,
        LeadStatus.CLICKED: 4,
        LeadStatus.RESPONDED: 5,
        LeadStatus.CALLING: 6,
        LeadStatus.CONNECTED: 7,
        LeadStatus.MEETING_SET: 8,
        LeadStatus.REJECTED: 9,
        LeadStatus.UNSUBSCRIBED: 10,
    }
    current_rank = STATUS_RANK.get(lead.status, 0)
    new_rank = STATUS_RANK.get(new_status, 0)

    # REJECTED/UNSUBSCRIBED は上書きしない
    if lead.status in (LeadStatus.REJECTED, LeadStatus.UNSUBSCRIBED):
        return
    if new_rank > current_rank:
        lead.status = new_status


def record_open(db: Session, token: str, ip: str, user_agent: str) -> bool:
    log = db.query(OutreachLog).filter(OutreachLog.tracking_token == token).first()
    if not log:
        return False

    if not log.opened_at:
        log.opened_at = datetime.utcnow()

    lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
    if lead:
        _update_lead_status_if_higher(lead, LeadStatus.OPENED)

    event = TrackingEvent(
        lead_id=log.lead_id,
        outreach_log_id=log.id,
        event_type="open",
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(event)
    db.commit()
    return True


def record_click(db: Session, token: str, url: str, ip: str, user_agent: str) -> str:
    """クリックを記録してリダイレクト先URLを返す"""
    log = db.query(OutreachLog).filter(OutreachLog.tracking_token == token).first()
    if not log:
        return url

    if not log.clicked_at:
        log.clicked_at = datetime.utcnow()

    lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
    if lead:
        _update_lead_status_if_higher(lead, LeadStatus.CLICKED)

    event = TrackingEvent(
        lead_id=log.lead_id,
        outreach_log_id=log.id,
        event_type="click",
        event_data=json.dumps({"url": url}),
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(event)
    db.commit()
    return url


def record_form_submit(db: Session, token: str, form_data: dict, ip: str) -> bool:
    """フォーム回答を記録 → ステータスをRESPONDED(HOT)に更新"""
    log = db.query(OutreachLog).filter(OutreachLog.tracking_token == token).first()
    if not log:
        return False

    if not log.responded_at:
        log.responded_at = datetime.utcnow()

    lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
    if lead:
        lead.status = LeadStatus.RESPONDED  # 常にHOTに昇格

    event = TrackingEvent(
        lead_id=log.lead_id,
        outreach_log_id=log.id,
        event_type="form_submit",
        event_data=json.dumps(form_data, ensure_ascii=False),
        ip_address=ip,
    )
    db.add(event)
    db.commit()
    return True


def record_unsubscribe(db: Session, token: str) -> bool:
    log = db.query(OutreachLog).filter(OutreachLog.tracking_token == token).first()
    if not log:
        return False

    lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
    if lead:
        lead.status = LeadStatus.UNSUBSCRIBED

    db.commit()
    return True
