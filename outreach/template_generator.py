"""
Claude APIを使ってリードごとにカスタマイズされたメール本文を生成する
"""
import anthropic
from database.models import Lead, ServiceType
from config import settings

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


SERVICE_DESCRIPTIONS = {
    ServiceType.SNS: "SNSアカウント運用代行",
    ServiceType.INFLUENCER: "インフルエンサーキャスティング",
    ServiceType.VIDEO_ADS: "タイアップ動画広告運用",
    ServiceType.MIXED: "SNS広告・インフルエンサー活用",
}

SERVICE_PAIN_POINTS = {
    ServiceType.SNS: "SNS担当者の工数削減・投稿品質の安定化・フォロワー獲得",
    ServiceType.INFLUENCER: "認知拡大・ターゲット層へのリーチ・費用対効果の高いPR",
    ServiceType.VIDEO_ADS: "動画コンテンツの制作〜配信まで一気通貫・再生数の最大化",
    ServiceType.MIXED: "複数SNSチャネルの統合管理・インフルエンサーと広告の相乗効果",
}


def _build_prompt(lead: Lead, service: ServiceType) -> str:
    sns_status_parts = []
    if lead.has_instagram:
        sns_status_parts.append("Instagram")
    if lead.has_twitter:
        sns_status_parts.append("X(Twitter)")
    if lead.has_tiktok:
        sns_status_parts.append("TikTok")
    if lead.has_youtube:
        sns_status_parts.append("YouTube")
    if lead.has_line_official:
        sns_status_parts.append("LINE公式")
    sns_status = "・".join(sns_status_parts) if sns_status_parts else "SNS未活用"

    ads_status_parts = []
    if lead.runs_web_ads:
        ads_status_parts.append("Web広告運用中")
    if lead.runs_video_ads:
        ads_status_parts.append("動画広告運用中")
    if lead.uses_influencer:
        ads_status_parts.append("インフルエンサー活用経験あり")
    ads_status = "・".join(ads_status_parts) if ads_status_parts else "デジタル広告未運用"

    service_name = SERVICE_DESCRIPTIONS[service]
    pain_points = SERVICE_PAIN_POINTS[service]

    return f"""あなたはWeb広告代理店の営業担当者です。
以下の見込み顧客企業に対して、初回メール営業文を作成してください。

【見込み顧客情報】
- 会社名: {lead.company_name}
- 業種: {lead.industry or "不明"}
- 従業員規模: {lead.employee_count or "不明"}
- 都道府県: {lead.prefecture or "不明"}
- SNS活用状況: {sns_status}
- 投稿頻度: {lead.sns_post_frequency or "不明"}
- 広告運用状況: {ads_status}

【提案サービス】
{service_name}

【ペインポイント・訴求軸】
{pain_points}

【メール作成の条件】
1. 件名と本文を両方作成すること
2. 件名は相手が開封したくなる具体的な内容にすること（30文字以内）
3. 本文は300〜400文字程度のコンパクトな構成にすること
4. 冒頭で相手企業の現状・課題に触れ、共感を示すこと
5. 自社サービスの具体的な成果・実績を1つ盛り込むこと（架空でOK）
6. CTA（行動喚起）は「15分程度のオンライン説明会」への誘導にすること
7. 高圧的・押し付けがましい表現は避け、ソフトな提案トーンにすること
8. 署名は含めないこと

以下のJSON形式で出力すること:
{{
  "subject": "件名",
  "body": "本文（プレーンテキスト）",
  "body_html": "本文（HTMLタグ付き、<p>タグで段落分け）"
}}
"""


def generate_email(lead: Lead, service: ServiceType | None = None) -> dict:
    """
    Claude APIを使ってリードに最適化されたメールを生成する
    Returns: {"subject": str, "body": str, "body_html": str}
    """
    if service is None:
        service = lead.recommended_service or ServiceType.MIXED

    prompt = _build_prompt(lead, service)
    client = get_client()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        system="あなたは優秀なB2B営業メールライターです。必ずJSON形式で出力してください。",
    )

    import json
    raw = message.content[0].text.strip()

    # JSONブロックを抽出
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    return {
        "subject": result.get("subject", ""),
        "body": result.get("body", ""),
        "body_html": result.get("body_html", ""),
        "service_type": service,
    }


def generate_emails_batch(leads: list[Lead]) -> list[dict]:
    """複数リードのメールを一括生成"""
    results = []
    for lead in leads:
        try:
            result = generate_email(lead)
            result["lead_id"] = lead.id
            result["success"] = True
        except Exception as e:
            result = {"lead_id": lead.id, "success": False, "error": str(e)}
        results.append(result)
    return results
