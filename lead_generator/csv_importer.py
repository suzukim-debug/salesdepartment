"""
CSVからリードをインポートする
対応フォーマット: UTF-8またはShift-JIS
"""
import io
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import Lead, LeadStatus
from lead_generator.scorer import batch_score_leads


COLUMN_MAP = {
    # CSV列名 → Leadフィールド名
    "会社名": "company_name",
    "company_name": "company_name",
    "業種": "industry",
    "industry": "industry",
    "都道府県": "prefecture",
    "prefecture": "prefecture",
    "住所": "address",
    "address": "address",
    "従業員数": "employee_count",
    "employee_count": "employee_count",
    "年商": "annual_revenue",
    "annual_revenue": "annual_revenue",
    "URL": "website",
    "ホームページ": "website",
    "website": "website",
    "担当者名": "contact_name",
    "contact_name": "contact_name",
    "役職": "contact_title",
    "contact_title": "contact_title",
    "メールアドレス": "contact_email",
    "email": "contact_email",
    "contact_email": "contact_email",
    "電話番号": "company_phone",
    "TEL": "company_phone",
    "company_phone": "company_phone",
    "担当者電話": "contact_phone",
    "contact_phone": "contact_phone",
    "Instagram": "has_instagram",
    "Twitter": "has_twitter",
    "X(Twitter)": "has_twitter",
    "TikTok": "has_tiktok",
    "YouTube": "has_youtube",
    "LINE公式": "has_line_official",
    "投稿頻度": "sns_post_frequency",
    "sns_post_frequency": "sns_post_frequency",
    "Web広告": "runs_web_ads",
    "runs_web_ads": "runs_web_ads",
    "動画広告": "runs_video_ads",
    "runs_video_ads": "runs_video_ads",
    "インフルエンサー活用": "uses_influencer",
    "uses_influencer": "uses_influencer",
    "メモ": "memo",
    "memo": "memo",
}

BOOL_TRUE_VALUES = {"yes", "true", "1", "あり", "○", "◯", "有"}


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in BOOL_TRUE_VALUES


def import_from_csv(
    filepath_or_buffer,
    db: Session,
    source: str = "csv_import",
    skip_duplicates: bool = True,
) -> dict:
    """
    CSVファイルをインポートしてDBに保存する
    Returns: {"imported": int, "skipped": int, "errors": list}
    """
    try:
        df = pd.read_csv(filepath_or_buffer, encoding="utf-8-sig")
    except UnicodeDecodeError:
        if hasattr(filepath_or_buffer, "seek"):
            filepath_or_buffer.seek(0)
        df = pd.read_csv(filepath_or_buffer, encoding="shift-jis")

    df.columns = [c.strip() for c in df.columns]

    imported = 0
    skipped = 0
    errors = []
    new_leads = []

    for idx, row in df.iterrows():
        try:
            lead_data = {"source": source, "status": LeadStatus.NEW}

            for col, val in row.items():
                field = COLUMN_MAP.get(col)
                if not field or pd.isna(val):
                    continue

                if field.startswith("has_") or field.startswith("runs_") or field == "uses_influencer":
                    lead_data[field] = _parse_bool(val)
                else:
                    lead_data[field] = str(val).strip()

            company_name = lead_data.get("company_name")
            if not company_name:
                errors.append(f"行{idx + 2}: 会社名が空のためスキップ")
                skipped += 1
                continue

            if skip_duplicates:
                existing = db.query(Lead).filter(Lead.company_name == company_name).first()
                if existing:
                    skipped += 1
                    continue

            lead = Lead(**lead_data)
            new_leads.append(lead)

        except Exception as e:
            errors.append(f"行{idx + 2}: {e}")

    # 一括スコアリング
    batch_score_leads(new_leads)

    for lead in new_leads:
        db.add(lead)
    db.flush()
    imported = len(new_leads)

    return {"imported": imported, "skipped": skipped, "errors": errors}


def get_csv_template() -> str:
    """インポート用CSVテンプレートのヘッダーを返す"""
    headers = [
        "会社名", "業種", "都道府県", "住所", "従業員数", "年商",
        "URL", "担当者名", "役職", "メールアドレス", "電話番号", "担当者電話",
        "Instagram", "Twitter", "TikTok", "YouTube", "LINE公式",
        "投稿頻度", "Web広告", "動画広告", "インフルエンサー活用", "メモ"
    ]
    return ",".join(headers) + "\n"
