"""
リードスコアリングエンジン
業種・規模・SNS活用状況から確度スコア(0〜100)と推奨サービスを算出する
"""
from database.models import Lead, ServiceType


# 高確度業種 (ECや消費財はSNS広告との親和性が高い)
HIGH_VALUE_INDUSTRIES = {
    "EC・通販": 25,
    "アパレル・ファッション": 23,
    "美容・コスメ": 23,
    "フード・飲食": 20,
    "フィットネス・スポーツ": 20,
    "ゲーム・エンタメ": 20,
    "旅行・宿泊": 18,
    "不動産": 15,
    "教育・スクール": 15,
    "医療・クリニック": 12,
    "金融・保険": 10,
    "製造業": 8,
    "IT・SaaS": 15,
    "人材・採用": 12,
}

SNS_FREQUENCY_SCORES = {
    "毎日": 15,
    "週3-5": 12,
    "週1-3": 8,
    "月数回": 4,
    "ほぼなし": 0,
    "なし": 0,
}

EMPLOYEE_SCORES = {
    "1-9": 5,
    "10-49": 12,
    "50-99": 15,
    "100-299": 18,
    "300-999": 15,
    "1000以上": 10,
}


def score_lead(lead: Lead) -> tuple[float, ServiceType]:
    """
    スコア計算 + 推奨サービス判定
    Returns: (score: float, service: ServiceType)
    """
    score = 0.0

    # 1. 業種スコア (最大25点)
    industry = lead.industry or ""
    for key, pts in HIGH_VALUE_INDUSTRIES.items():
        if key in industry:
            score += pts
            break
    else:
        score += 5  # 未分類業種はデフォルト5点

    # 2. SNS活用状況スコア (最大30点)
    sns_count = sum([
        lead.has_instagram or False,
        lead.has_twitter or False,
        lead.has_tiktok or False,
        lead.has_youtube or False,
        lead.has_line_official or False,
    ])
    score += min(sns_count * 4, 15)  # SNS保有数 (最大15点)

    freq_score = SNS_FREQUENCY_SCORES.get(lead.sns_post_frequency or "なし", 0)
    score += freq_score  # 投稿頻度 (最大15点)

    # 3. 既存広告運用状況スコア (最大20点)
    if lead.runs_web_ads:
        score += 12   # Web広告を既に走らせている = 予算がある
    if lead.runs_video_ads:
        score += 5    # 動画広告経験あり
    if lead.uses_influencer:
        score += 3    # インフルエンサー活用経験あり

    # 4. 従業員規模スコア (最大18点)
    emp = lead.employee_count or ""
    for key, pts in EMPLOYEE_SCORES.items():
        if key in emp:
            score += pts
            break

    # 5. 連絡先情報の充実度 (最大7点)
    if lead.contact_email:
        score += 3
    if lead.contact_phone or lead.company_phone:
        score += 2
    if lead.contact_name:
        score += 2

    score = min(score, 100.0)

    # 推奨サービス判定
    service = _recommend_service(lead, score)

    return round(score, 1), service


def _recommend_service(lead: Lead, score: float) -> ServiceType:
    sns_active = sum([
        lead.has_instagram or False,
        lead.has_twitter or False,
        lead.has_tiktok or False,
    ]) >= 1

    if lead.uses_influencer or (lead.has_instagram and lead.has_tiktok):
        return ServiceType.INFLUENCER
    elif lead.runs_video_ads or lead.has_youtube:
        return ServiceType.VIDEO_ADS
    elif sns_active:
        return ServiceType.SNS
    else:
        return ServiceType.MIXED


def batch_score_leads(leads: list[Lead]) -> None:
    """リストを一括スコアリングして各Leadオブジェクトを更新する"""
    for lead in leads:
        lead.lead_score, lead.recommended_service = score_lead(lead)
