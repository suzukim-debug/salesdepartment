"""
CSVからリードをインポートする
対応フォーマット: UTF-8またはShift-JIS
"""
import io
import json
import pandas as pd
from sqlalchemy.orm import Session
from database.models import Lead, LeadStatus
from lead_generator.scorer import batch_score_leads


COLUMN_MAP = {
    # 会社名
    "会社名": "company_name",
    "商号又は名称": "company_name",
    "商号・名称": "company_name",
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
    "ジャンル一覧": "industry",
    "ジャンル": "industry",
    "industry": "industry",
    # 地域
    "都道府県": "prefecture",
    "prefecture": "prefecture",
    "住所": "address",
    "所在地": "address",
    "address": "address",
    "郵便番号": "_postal",  # 住所に付加
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
    "フリガナ": "_skip",
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
    "携帯番号": "contact_phone",
    "担当者電話": "contact_phone",
    "contact_phone": "contact_phone",
    # 規模
    "従業員数": "employee_count",
    "従業員": "employee_count",
    "employee_count": "employee_count",
    "年商": "annual_revenue",
    "売上": "annual_revenue",
    "annual_revenue": "annual_revenue",
    # SNS（bool）
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

# 広告プラットフォーム消化額列（sns_raw_dataに保存）
AD_SPEND_COLS = [
    "予想消化額合計 (YouTube)",
    "予想消化額合計 (TikTok)",
    "予想消化額合計 (Shorts)",
    "予想消化額合計 (Instagram)",
    "予想消化額合計 (Facebook)",
    "予想消化額合計 (Pangle)",
    "予想消化額合計 (LAP)",
    "予想消化額合計 (X)",
    "予想消化額合計 (SmartNews)",
    "予想消化額合計 (Yahoo)",
    "予想消化額合計 (Pinterest)",
    "予想消化額合計 (Google)",
    "予想消化額合計 (Jimoty)",
    "予想消化額合計 (Mercari)",
    "予想消化額合計",
    "予想消化額増加 (30日間)",
    "クリエイティブ総数",
    "商材一覧",
]

BOOL_TRUE_VALUES = {"yes", "true", "1", "あり", "○", "◯", "有", "✓", "✔"}


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in BOOL_TRUE_VALUES


def _parse_num(val) -> float:
    try:
        return float(str(val).replace(",", "").replace("¥", "").strip())
    except Exception:
        return 0.0


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
    if not any(COLUMN_MAP.get(c) == "company_name" for c in df.columns):
        COLUMN_MAP[df.columns[0]] = "company_name"

    # 広告消化額列を検出
    ad_cols_present = [c for c in df.columns if c in AD_SPEND_COLS]

    imported = 0
    skipped = 0
    errors = []
    new_leads = []

    for idx, row in df.iterrows():
        try:
            lead_data = {"source": source, "status": LeadStatus.NEW}
            is_excluded = False
            ad_spend = {}

            for col, val in row.items():
                if pd.isna(val):
                    continue

                # 広告消化額列
                if col in ad_cols_present:
                    num = _parse_num(val)
                    if num > 0:
                        ad_spend[col] = num
                    continue

                field = COLUMN_MAP.get(col)
                if not field or field == "_skip":
                    continue

                if field == "_exclude":
                    is_excluded = _parse_bool(val)
                elif field == "_postal":
                    # 郵便番号は住所に付加
                    existing_addr = lead_data.get("address", "")
                    lead_data["address"] = f"〒{val} {existing_addr}".strip()
                elif field.startswith("has_") or field.startswith("runs_") or field == "uses_influencer":
                    lead_data[field] = _parse_bool(val)
                else:
                    lead_data[field] = str(val).strip()

            if is_excluded:
                lead_data["status"] = LeadStatus.EXCLUDED

            company_name = lead_data.get("company_name")
            if not company_name:
                skipped += 1
                continue

            if skip_duplicates:
                existing = db.query(Lead).filter(Lead.company_name == company_name).first()
                if existing:
                    skipped += 1
                    continue

            # 広告消化額データをsns_raw_dataに保存
            if ad_spend:
                lead_data["sns_raw_data"] = {"ad_spend": ad_spend}
                # 広告運用中フラグを自動設定
                total = ad_spend.get("予想消化額合計", 0)
                if total > 0:
                    lead_data["runs_web_ads"] = True
                if ad_spend.get("予想消化額合計 (YouTube)", 0) > 0 or \
                   ad_spend.get("予想消化額合計 (TikTok)", 0) > 0 or \
                   ad_spend.get("予想消化額合計 (Shorts)", 0) > 0:
                    lead_data["runs_video_ads"] = True
                if ad_spend.get("予想消化額合計 (Instagram)", 0) > 0:
                    lead_data["has_instagram"] = True

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
