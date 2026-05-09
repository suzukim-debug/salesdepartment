from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime,
    Boolean, ForeignKey, Enum, JSON
)
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class LeadStatus(str, PyEnum):
    NEW = "new"                    # 新規
    SCRAPING = "scraping"          # スクレイピング中
    RESEARCHED = "researched"      # 情報収集済み
    EMAIL_SENT = "email_sent"      # メール送信済み
    OPENED = "opened"              # 開封済み
    CLICKED = "clicked"            # リンククリック済み
    REPLIED = "replied"            # 返信あり
    RESPONDED = "responded"        # フォーム回答 (HOT)
    FOLLOWUP_SENT = "followup_sent"  # フォローアップ送信済み
    CALLING = "calling"            # 架電中
    CONNECTED = "connected"        # 繋がった
    MEETING_SET = "meeting_set"    # アポ獲得
    REJECTED = "rejected"          # お断り
    UNSUBSCRIBED = "unsubscribed"  # 配信停止


class ServiceType(str, PyEnum):
    SNS = "sns"
    INFLUENCER = "influencer"
    VIDEO_ADS = "video_ads"
    MIXED = "mixed"


class ScrapeStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    company_name = Column(String(200), nullable=False)
    industry = Column(String(100))
    website = Column(String(500))
    employee_count = Column(String(50))
    annual_revenue = Column(String(50))
    prefecture = Column(String(50))
    address = Column(String(500))

    # 担当者情報
    contact_name = Column(String(100))
    contact_title = Column(String(100))
    contact_email = Column(String(200))
    contact_phone = Column(String(50))
    company_phone = Column(String(50))

    # SNS・デジタル活用状況
    has_instagram = Column(Boolean, default=False)
    has_twitter = Column(Boolean, default=False)
    has_tiktok = Column(Boolean, default=False)
    has_youtube = Column(Boolean, default=False)
    has_line_official = Column(Boolean, default=False)
    instagram_url = Column(String(500))
    twitter_url = Column(String(500))
    tiktok_url = Column(String(500))
    youtube_url = Column(String(500))
    sns_post_frequency = Column(String(50))
    sns_follower_estimate = Column(String(50))     # スクレイピングで取得したフォロワー概算
    uses_influencer = Column(Boolean, default=False)
    runs_video_ads = Column(Boolean, default=False)
    runs_web_ads = Column(Boolean, default=False)
    is_hiring_marketer = Column(Boolean, default=False)  # 求人でマーケ職募集中

    # スコアリング
    lead_score = Column(Float, default=0.0)
    ai_analysis = Column(Text)                    # AI分析コメント
    recommended_service = Column(Enum(ServiceType))
    budget_estimate = Column(String(50))          # 推定予算規模

    # スクレイピング状態
    scrape_status = Column(Enum(ScrapeStatus), default=ScrapeStatus.PENDING)
    scraped_at = Column(DateTime)
    scrape_error = Column(Text)

    # SNS深掘り分析
    sns_raw_data = Column(JSON)                   # 各SNSページのスクレイピング生データ
    sns_diagnosis = Column(JSON)                  # Claude生成のSNS診断レポート
    sns_analyzed_at = Column(DateTime)            # SNS分析実施日時

    # Gmail連携
    gmail_thread_ids = Column(JSON, default=list)  # 送信スレッドID一覧

    # Sheetsエクスポート
    sheets_row = Column(Integer)                  # スプレッドシートの行番号

    # ステータス
    status = Column(Enum(LeadStatus), default=LeadStatus.NEW)
    memo = Column(Text)
    source = Column(String(100))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    outreach_logs = relationship("OutreachLog", back_populates="lead", cascade="all, delete-orphan")
    call_logs = relationship("CallLog", back_populates="lead", cascade="all, delete-orphan")
    tracking_events = relationship("TrackingEvent", back_populates="lead", cascade="all, delete-orphan")
    follow_up_logs = relationship("FollowUpLog", back_populates="lead", cascade="all, delete-orphan")
    scrape_jobs = relationship("ScrapeJob", back_populates="lead", cascade="all, delete-orphan")


class OutreachLog(Base):
    __tablename__ = "outreach_logs"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    service_type = Column(Enum(ServiceType))
    subject = Column(String(500))
    body_html = Column(Text)
    body_text = Column(Text)
    sent_at = Column(DateTime)
    scheduled_at = Column(DateTime)               # スケジュール送信時刻
    tracking_token = Column(String(64), unique=True)
    gmail_message_id = Column(String(200))        # Gmail API メッセージID
    gmail_thread_id = Column(String(200))         # Gmail スレッドID
    opened_at = Column(DateTime)
    clicked_at = Column(DateTime)
    replied_at = Column(DateTime)                 # 返信検知時刻
    responded_at = Column(DateTime)
    followup_sent_at = Column(DateTime)           # フォローアップ送信時刻
    is_followup = Column(Boolean, default=False)  # フォローアップメールか
    error = Column(Text)
    sequence_number = Column(Integer, default=1)  # 1=初回, 2=フォロー1回目, ...

    lead = relationship("Lead", back_populates="outreach_logs")


class TrackingEvent(Base):
    __tablename__ = "tracking_events"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    outreach_log_id = Column(Integer, ForeignKey("outreach_logs.id"))
    event_type = Column(String(50))     # "open", "click", "form_submit", "reply"
    event_data = Column(Text)
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
    result = Column(String(100))
    next_action = Column(String(200))
    next_action_date = Column(DateTime)
    notes = Column(Text)
    synced_to_sheets = Column(Boolean, default=False)

    lead = relationship("Lead", back_populates="call_logs")


class FollowUpLog(Base):
    """7日後フォローアップの実行記録"""
    __tablename__ = "follow_up_logs"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    outreach_log_id = Column(Integer, ForeignKey("outreach_logs.id"))
    follow_up_number = Column(Integer, default=1)  # 何回目のフォローアップか
    scheduled_at = Column(DateTime)
    sent_at = Column(DateTime)
    result = Column(String(50))                    # "sent", "skipped", "error"
    skip_reason = Column(String(200))              # スキップ理由

    lead = relationship("Lead", back_populates="follow_up_logs")


class ScrapeJob(Base):
    """スクレイピングジョブの実行履歴"""
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    target_url = Column(String(500))
    scrape_type = Column(String(50))              # "website", "job_site", "sns"
    status = Column(Enum(ScrapeStatus), default=ScrapeStatus.PENDING)
    result_data = Column(JSON)                    # スクレイピング結果
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="scrape_jobs")


class SchedulerJob(Base):
    """スケジューラジョブの状態管理"""
    __tablename__ = "scheduler_jobs"

    id = Column(Integer, primary_key=True)
    job_name = Column(String(100), unique=True)
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)
    last_result = Column(String(200))
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
