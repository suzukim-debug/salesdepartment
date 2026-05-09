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
    # 会社名（多様な表記に対応）
    "会社名": "company_name",
    "企業名": "company_name",
    "社名": "company_name",
    "店舗名": "company_name",
    "法人名": "company_name",
    "屋号": "company_name",
    "事業者名": "company_name",
    "company_name": "company_name",
    "name": "company_name",
    "Name": "company_name",
    # 業種
    "業種": "industry",
    "業態": "industry",
    "カテゴリ": "industry",
    "industry": "industry",
    # 地域
    "都道府県": "prefecture",
    "prefecture": "prefecture",
    "住所": "address",
    "所在地": "address",
    "address": "address",
    # 規模
    "従業員数": "employee_count",
    "従業員": "employee_count",
    "employee_count": "employee_count",
    "年商": "annual_revenue",
    "売上": "annual_revenue",
    "annual_revenue": "annual_revenue",
    # ウェブサイト
    "URL": "website",
    "url": "website",
    "HP": "website",
    "ホームページ": "website",
    "サイト": "website",
    "ウェブサイト": "website",
    "website": "website",
    # 担当者
    "担当者名": "contact_name",
    "担当者": "contact_name",
    "contact_name": "contact_name",
    "役職": "contact_title",
    "contact_title": "contact_title",
    # メール
    "メールアドレス": "contact_email",
    "メール": "contact_email",
    "Email": "contact_email",
    "email": "contact_email",
    "E-mail": "contact_email",
    "e-mail": "contact_email",
    "Mail": "contact_email",
    "mail": "contact_email",
    "contact_email": "contact_email",
    # 電話
    "電話番号": "company_phone",
    "電話": "company_phone",
    "TEL": "company_phone",
    "tel": "company_phone",
    "Tel": "company_phone",
    "phone": "company_phone",
    "Phone": "company_phone",
    "company_phone": "company_phone",
    "担当者電話": "contact_phone",
    "contact_phone": "contact_phone",
    # SNS
    "Instagram": "has_instagram",
    "instagram": "has_instagram",
    "Twitter": "has_twitter",
    "twitter": "has_twitter",
    "X(Twitter)": "has_twitter",
    "X": "has_twitter",
    "TikTok": "has_tiktok",
    "tiktok": "has_tiktok",
    "YouTube": "has_youtube",
    "youtube": "has_youtube",
    "LINE公式": "has_line_official",
    "LINE": "has_line_official",
    # その他
    "投稿頻度": "sns_post_frequency",
    "sns_post_frequency": "sns_post_frequency",
    "Web広告": "runs_web_ads",
    "web広告": "runs_web_ads",
    "runs_web_ads": "runs_web_ads",
    "動画広告": "runs_video_ads",
    "runs_video_ads": "runs_video_ads",
    "インフルエンサー活用": "uses_influencer",
    "uses_influencer": "uses_influencer",
    "メモ": "memo",
    "備考": "memo",
    "note": "memo",
    "memo": "memo",
    # 除外フラグ
    "除外": "_exclude",
    "取引中": "_exclude",
    "既存取引先": "_exclude",
    "既存客": "_exclude",
    "対象外": "_exclude",
    "exclude": "_exclude",
}

BOOL_TRUE_VALUES = {"yes", "true", "1", "あり", "○", "◯", "有", "✓", "✔"}


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

    # 列名が一致しない場合、最初の列を会社名として自動認識
    mapped_cols = {c for c in df.columns if c in COLUMN_MAP}
    if not any(COLUMN_MAP.get(c) == "company_name" for c in mapped_cols):
        first_col = df.columns[0]
        COLUMN_MAP[first_col] = "company_name"

    imported = 0
    skipped = 0
    errors = []
    new_leads = []

    for idx, row in df.iterrows():
        try:
            lead_data = {"source": source, "status": LeadStatus.NEW}

            is_excluded = False
            for col, val in row.items():
                field = COLUMN_MAP.get(col)
                if not field or pd.isna(val):
                    continue

                if field == "_exclude":
                    is_excluded = _parse_bool(val)
                elif field.startswith("has_") or field.startswith("runs_") or field == "uses_influencer":
                    lead_data[field] = _parse_bool(val)
                else:
                    lead_data[field] = str(val).strip()

            if is_excluded:
                lead_data["status"] = LeadStatus.EXCLUDED

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
        "投稿頻度", "Web広告", "動画広告", "インフルエンサー活用", "メモ", "除外"
    ]
    return ",".join(headers) + "\n"
