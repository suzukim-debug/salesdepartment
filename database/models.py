from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime,
    Boolean, ForeignKey, Enum
)
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class LeadStatus(str, PyEnum):
    NEW = "new"                    # 新規
    RESEARCHED = "researched"      # 情報収集済み
    EMAIL_SENT = "email_sent"      # メール送信済み
    OPENED = "opened"              # 開封済み
    CLICKED = "clicked"            # リンククリック済み
    RESPONDED = "responded"        # 反響あり (HOT)
    CALLING = "calling"            # 架電中
    CONNECTED = "connected"        # 繋がった
    MEETING_SET = "meeting_set"    # アポ獲得
    REJECTED = "rejected"          # お断り
    UNSUBSCRIBED = "unsubscribed"  # 配信停止


class ServiceType(str, PyEnum):
    SNS = "sns"                  # SNSアカウント運用
    INFLUENCER = "influencer"    # インフルエンサーキャスティング
    VIDEO_ADS = "video_ads"      # タイアップ動画広告
    MIXED = "mixed"              # 複合提案


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    company_name = Column(String(200), nullable=False)
    industry = Column(String(100))
    website = Column(String(500))
    employee_count = Column(String(50))          # "10-50" など
    annual_revenue = Column(String(50))          # "1億〜5億" など
    prefecture = Column(String(50))
    address = Column(String(500))

    # 担当者情報
    contact_name = Column(String(100))
    contact_title = Column(String(100))
    contact_email = Column(String(200))
    contact_phone = Column(String(50))
    company_phone = Column(String(50))

    # SNS・デジタル活用状況 (スコアリング用)
    has_instagram = Column(Boolean, default=False)
    has_twitter = Column(Boolean, default=False)
    has_tiktok = Column(Boolean, default=False)
    has_youtube = Column(Boolean, default=False)
    has_line_official = Column(Boolean, default=False)
    sns_post_frequency = Column(String(50))       # "毎日", "週1-3", "月数回", "なし"
    uses_influencer = Column(Boolean, default=False)
    runs_video_ads = Column(Boolean, default=False)
    runs_web_ads = Column(Boolean, default=False)

    # スコアリング
    lead_score = Column(Float, default=0.0)       # 0〜100
    recommended_service = Column(Enum(ServiceType))

    # ステータス管理
    status = Column(Enum(LeadStatus), default=LeadStatus.NEW)
    memo = Column(Text)

    # メタ
    source = Column(String(100))                  # "csv_import", "manual", etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    outreach_logs = relationship("OutreachLog", back_populates="lead", cascade="all, delete-orphan")
    call_logs = relationship("CallLog", back_populates="lead", cascade="all, delete-orphan")
    tracking_events = relationship("TrackingEvent", back_populates="lead", cascade="all, delete-orphan")


class OutreachLog(Base):
    __tablename__ = "outreach_logs"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    service_type = Column(Enum(ServiceType))
    subject = Column(String(500))
    body_html = Column(Text)
    body_text = Column(Text)
    sent_at = Column(DateTime)
    tracking_token = Column(String(64), unique=True)  # 開封トラッキング用
    opened_at = Column(DateTime)
    clicked_at = Column(DateTime)
    responded_at = Column(DateTime)
    error = Column(Text)

    lead = relationship("Lead", back_populates="outreach_logs")


class TrackingEvent(Base):
    __tablename__ = "tracking_events"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    outreach_log_id = Column(Integer, ForeignKey("outreach_logs.id"))
    event_type = Column(String(50))     # "open", "click", "form_submit"
    event_data = Column(Text)           # JSON文字列
    ip_address = Column(String(50))
    user_agent = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="tracking_events")


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    called_at = Column(DateTime, default=datetime.utcnow)
    caller_name = Column(String(100))
    duration_seconds = Column(Integer, default=0)
    result = Column(String(100))        # "繋がらず", "折り返し", "アポ獲得", "お断り", "再架電"
    next_action = Column(String(200))
    next_action_date = Column(DateTime)
    notes = Column(Text)

    lead = relationship("Lead", back_populates="call_logs")
