"""
Google Sheets エクスポーター
ホットリードをスコア順でスプレッドシートに書き出す（⑥テレアポリスト優先化）
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database.models import Lead, LeadStatus
from sheets_integration.client import get_sheets_client, get_or_create_spreadsheet, get_or_create_worksheet
from config import settings

logger = logging.getLogger(__name__)

HOT_LEADS_HEADERS = [
    "優先度", "会社名", "業種", "スコア", "推奨サービス",
    "担当者名", "役職", "電話番号（担当者）", "電話番号（代表）",
    "メールアドレス", "都道府県", "従業員数", "推定予算",
    "Instagram", "TikTok", "YouTube", "LINE",
    "Web広告運用", "求人マーケ職あり",
    "ステータス", "最終送信日", "開封", "クリック", "返信",
    "メモ", "更新日時", "SystemID",
]

CALL_LOG_HEADERS = [
    "架電日時", "会社名", "担当者", "架電担当", "結果",
    "通話時間(秒)", "次回アクション", "次回日時", "メモ", "SystemID",
]

RESULTS_HEADERS = [
    "集計日", "総リード数", "メール送信数", "開封数", "クリック数",
    "返信数", "フォーム回答数", "架電数", "アポ獲得数",
    "開封率(%)", "返信率(%)", "アポ率(%)",
]


def _lead_to_row(rank: int, lead: Lead) -> list:
    """LeadオブジェクトをSheetsの行に変換"""
    latest_log = (
        sorted(lead.outreach_logs, key=lambda x: x.sent_at or datetime.min, reverse=True)[0]
        if lead.outreach_logs else None
    )

    return [
        rank,
        lead.company_name,
        lead.industry or "",
        lead.lead_score,
        lead.recommended_service.value if lead.recommended_service else "",
        lead.contact_name or "",
        lead.contact_title or "",
        lead.contact_phone or "",
        lead.company_phone or "",
        lead.contact_email or "",
        lead.prefecture or "",
        lead.employee_count or "",
        lead.budget_estimate or "",
        "✓" if lead.has_instagram else "",
        "✓" if lead.has_tiktok else "",
        "✓" if lead.has_youtube else "",
        "✓" if lead.has_line_official else "",
        "✓" if lead.runs_web_ads else "",
        "✓" if lead.is_hiring_marketer else "",
        lead.status.value,
        latest_log.sent_at.strftime("%Y/%m/%d") if latest_log and latest_log.sent_at else "",
        "✓" if (latest_log and latest_log.opened_at) else "",
        "✓" if (latest_log and latest_log.clicked_at) else "",
        "✓" if (latest_log and latest_log.replied_at) else "",
        lead.memo or "",
        datetime.utcnow().strftime("%Y/%m/%d %H:%M"),
        str(lead.id),
    ]


def export_hot_leads_to_sheets(db: Session, min_score: float = 0) -> dict:
    """
    ホットリード・送信済みリードをスプレッドシートに書き出す（⑥テレアポリスト）

    Returns: {"rows_written": int, "spreadsheet_url": str}
    """
    hot_statuses = [
        LeadStatus.RESPONDED, LeadStatus.REPLIED, LeadStatus.CLICKED,
        LeadStatus.OPENED, LeadStatus.EMAIL_SENT, LeadStatus.CALLING,
        LeadStatus.CONNECTED,
    ]

    leads = (
        db.query(Lead)
        .filter(
            Lead.status.in_(hot_statuses),
            Lead.lead_score >= min_score,
        )
        .order_by(desc(Lead.lead_score))
        .all()
    )

    if not leads:
        return {"rows_written": 0, "spreadsheet_url": ""}

    ss = get_or_create_spreadsheet()
    ws = get_or_create_worksheet(ss, settings.sheets_hot_leads_tab, HOT_LEADS_HEADERS)

    # 全行クリア（ヘッダー行は残す）
    ws.clear()
    ws.append_row(HOT_LEADS_HEADERS)

    rows = []
    for rank, lead in enumerate(leads, 1):
        rows.append(_lead_to_row(rank, lead))
        lead.sheets_row = rank + 1  # ヘッダー行を考慮

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    # ヘッダー行のスタイル設定
    try:
        ws.format("A1:AA1", {
            "backgroundColor": {"red": 0.1, "green": 0.45, "blue": 0.9},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        })
        # 優先度の高い行（スコア70以上）を色付け
        for i, lead in enumerate(leads):
            if lead.lead_score >= 70:
                ws.format(f"A{i+2}:AA{i+2}", {
                    "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.9}
                })
    except Exception:
        pass  # スタイル設定は失敗しても続行

    db.commit()
    return {
        "rows_written": len(rows),
        "spreadsheet_url": ss.url,
    }


def sync_call_logs_to_sheets(db: Session) -> dict:
    """
    未同期の架電ログをスプレッドシートに追記する（⑧結果記録）
    """
    from database.models import CallLog

    unsynced = (
        db.query(CallLog)
        .filter(CallLog.synced_to_sheets == False)
        .order_by(CallLog.called_at)
        .all()
    )

    if not unsynced:
        return {"rows_written": 0}

    ss = get_or_create_spreadsheet()
    ws = get_or_create_worksheet(ss, settings.sheets_call_log_tab, CALL_LOG_HEADERS)

    rows = []
    for cl in unsynced:
        lead = db.query(Lead).filter(Lead.id == cl.lead_id).first()
        rows.append([
            cl.called_at.strftime("%Y/%m/%d %H:%M") if cl.called_at else "",
            lead.company_name if lead else "",
            lead.contact_name if lead else "",
            cl.caller_name or "",
            cl.result or "",
            cl.duration_seconds or 0,
            cl.next_action or "",
            cl.next_action_date.strftime("%Y/%m/%d %H:%M") if cl.next_action_date else "",
            cl.notes or "",
            str(cl.lead_id),
        ])
        cl.synced_to_sheets = True

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    db.commit()
    return {"rows_written": len(rows)}


def write_analytics_to_sheets(db: Session) -> dict:
    """
    分析サマリーをスプレッドシートに書き込む（⑧分析）
    """
    from database.models import OutreachLog, CallLog
    from sqlalchemy import func

    total = db.query(func.count(Lead.id)).scalar()
    sent = db.query(func.count(OutreachLog.id)).filter(OutreachLog.sent_at.isnot(None)).scalar()
    opened = db.query(func.count(OutreachLog.id)).filter(OutreachLog.opened_at.isnot(None)).scalar()
    clicked = db.query(func.count(OutreachLog.id)).filter(OutreachLog.clicked_at.isnot(None)).scalar()
    replied = db.query(func.count(OutreachLog.id)).filter(OutreachLog.replied_at.isnot(None)).scalar()
    responded = db.query(func.count(Lead.id)).filter(Lead.status == LeadStatus.RESPONDED).scalar()
    calls = db.query(func.count(CallLog.id)).scalar()
    meetings = db.query(func.count(Lead.id)).filter(Lead.status == LeadStatus.MEETING_SET).scalar()

    open_rate = round(opened / sent * 100, 1) if sent > 0 else 0
    reply_rate = round(replied / sent * 100, 1) if sent > 0 else 0
    apo_rate = round(meetings / calls * 100, 1) if calls > 0 else 0

    ss = get_or_create_spreadsheet()
    ws = get_or_create_worksheet(ss, settings.sheets_results_tab, RESULTS_HEADERS)

    ws.append_row([
        datetime.utcnow().strftime("%Y/%m/%d"),
        total, sent, opened, clicked, replied, responded,
        calls, meetings,
        open_rate, reply_rate, apo_rate,
    ], value_input_option="USER_ENTERED")

    return {"spreadsheet_url": ss.url}
