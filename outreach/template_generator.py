"""
AI APIを使ってリードごとにカスタマイズされたメール本文を生成する
Claude API または Google Gemini API を使用（設定に応じて自動切替）
SNS診断データが存在する場合は改善施策を含む具体的な提案メールを生成する
"""
import json
import logging
from database.models import Lead, ServiceType
from config import settings

logger = logging.getLogger(__name__)


def _call_ai(system_msg: str, prompt: str) -> str:
    """Claude または Gemini でテキスト生成（設定済みのAPIを自動選択）"""
    if settings.anthropic_api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_msg,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    elif settings.gemini_api_key:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_msg),
        )
        return response.text.strip()
    else:
        raise ValueError("ANTHROPIC_API_KEY または GEMINI_API_KEY を .env に設定してください")


SERVICE_DESCRIPTIONS = {
    ServiceType.SNS: "SNSアカウント運用代行",
    ServiceType.INFLUENCER: "インフルエンサーキャスティング",
    ServiceType.VIDEO_ADS: "タイアップ動画広告運用",
    ServiceType.MIXED: "SNS広告・インフルエンサー活用",
}


def _build_prompt_with_sns_diagnosis(lead: Lead, service: ServiceType, diagnosis: dict) -> str:
    """SNS診断レポートを活用した具体的な提案メールプロンプト"""

    # 診断サマリー
    diag_summary = diagnosis.get("diagnosis_summary", "")

    # 重大な問題（最大3件）
    critical = diagnosis.get("critical_issues", [])[:3]
    issues_text = "\n".join([
        f"  ・{c['platform']}: {c['issue']}" for c in critical
    ]) if critical else "  ・特になし"

    # 改善提案（最大3件）
    proposals = diagnosis.get("improvement_proposals", [])[:3]
    proposals_text = "\n".join([
        f"  ・{p['title']}: {p['detail']}（期待効果: {p['expected_result']}）"
        for p in proposals
    ]) if proposals else "  ・SNS運用の全体的な改善"

    # クイックウィン
    quick_wins = diagnosis.get("quick_wins", [])[:3]
    quick_wins_text = "\n".join([f"  {i+1}. {w}" for i, w in enumerate(quick_wins)]) \
        if quick_wins else ""

    # スコア
    current_score = diagnosis.get("overall_score", "?")
    potential_score = diagnosis.get("potential_score", "?")
    competitor_gap = diagnosis.get("competitor_gap", "")

    # SNS実データ
    sns_raw = lead.sns_raw_data or {}
    ig = sns_raw.get("instagram", {})
    ig_followers = ig.get("followers", "不明") if ig and not ig.get("error") else "不明"
    ig_freq = ig.get("post_frequency_hint", "不明") if ig else "不明"
    ig_engagement = ig.get("engagement_rate_estimate", "不明") if ig else "不明"

    sender_name = settings.gmail_from_name or settings.smtp_from_name or "鈴木"
    return f"""あなたはWeb広告代理店の敏腕営業担当者です。
以下の企業のSNSを実際に調査した結果をもとに、具体的な改善提案を含む初回営業メールを作成してください。

【送信者情報】
送信者名: {sender_name}
※ 本文の自己紹介は必ず「アドリブ株式会社の{sender_name}と申します。」と書いてください。[氏名]・〇〇・XXX等のプレースホルダーは絶対に使用禁止。

【宛先企業情報】
- 会社名: {lead.company_name}
- 業種: {lead.industry or "不明"}
- 提案サービス: {SERVICE_DESCRIPTIONS[service]}

【SNS実態調査結果（実際のアカウントを確認済み）】
{diag_summary}

【検出した具体的な問題点】
{issues_text}

【私たちが提案できる改善施策】
{proposals_text}

【競合との差】
{competitor_gap}

【SNS運用スコア】
現状: {current_score}点 → 施策実施後: {potential_score}点（推定）

{f"【すぐにできる改善】{chr(10)}{quick_wins_text}" if quick_wins else ""}

【実データ（参考）】
- Instagram フォロワー: {ig_followers}人
- 投稿頻度: {ig_freq}
- エンゲージメント率: {ig_engagement}%

【メール作成の絶対条件】
1. 冒頭で「貴社の{"{SNS名}"}を拝見しました」と実際に見た事実を示す
2. 具体的な数字（フォロワー数・投稿頻度・エンゲージメント率）を1〜2個必ず本文に入れる
3. 問題点を「気づき」として共感的に伝える（批判・指摘口調は絶対NG）
4. 改善後に「どうなるか」を数字付きで1つ示す
5. CTAは「10分のSNS無料診断」への誘導（送ったレポートの内容を踏まえて）
6. 本文は350〜450文字（簡潔だが具体的に）
7. 件名は開封率重視（「【{lead.company_name}様のInstagram診断】」のような形式も可）
8. 押し付けがましくなく、「一緒に解決したい」トーンで

以下のJSON形式で出力してください:
{{
  "subject": "件名（40文字以内）",
  "body": "本文プレーンテキスト（署名なし）",
  "body_html": "本文HTML（<p>タグで段落分け、<strong>で強調）",
  "key_insight_used": "メールで使った最も重要な実データ・発見（1文）"
}}
"""


def _build_prompt_basic(lead: Lead, service: ServiceType) -> str:
    """SNS診断データがない場合のベーシックプロンプト"""

    sns_parts = []
    if lead.has_instagram:
        sns_parts.append(f"Instagram（フォロワー: {lead.sns_follower_estimate or '不明'}）")
    if lead.has_twitter:
        sns_parts.append("X(Twitter)")
    if lead.has_tiktok:
        sns_parts.append("TikTok")
    if lead.has_youtube:
        sns_parts.append("YouTube")
    if lead.has_line_official:
        sns_parts.append("LINE公式")
    sns_status = "・".join(sns_parts) if sns_parts else "SNS未活用"

    ads_parts = []
    if lead.runs_web_ads:
        ads_parts.append("Web広告運用中")
    if lead.runs_video_ads:
        ads_parts.append("動画広告あり")
    if lead.uses_influencer:
        ads_parts.append("インフルエンサー活用経験あり")
    ads_status = "・".join(ads_parts) if ads_parts else "デジタル広告未運用"

    sender_name = settings.gmail_from_name or settings.smtp_from_name or "鈴木"
    return f"""あなたはWeb広告代理店の営業担当者です。
以下の見込み顧客に対して初回営業メールを作成してください。

【送信者情報】
送信者名: {sender_name}
※ 本文の自己紹介は必ず「アドリブ株式会社の{sender_name}と申します。」と書いてください。[氏名]・〇〇・XXX等のプレースホルダーは絶対に使用禁止。

【企業情報】
- 会社名: {lead.company_name}
- 業種: {lead.industry or "不明"}
- 従業員規模: {lead.employee_count or "不明"}
- SNS活用状況: {sns_status}
- 投稿頻度: {lead.sns_post_frequency or "不明"}
- 広告運用: {ads_status}
- 推定予算: {lead.budget_estimate or "不明"}
- 提案サービス: {SERVICE_DESCRIPTIONS[service]}

【条件】
1. 件名は開封したくなる内容（30文字以内）
2. 本文300〜400文字、冒頭で現状課題に共感
3. 具体的な成果事例を1つ（架空でOK）
4. CTAは「15分オンライン説明会」
5. ソフトなトーンで、署名なし

JSON形式で出力:
{{
  "subject": "件名",
  "body": "本文（プレーンテキスト）",
  "body_html": "本文（HTML、<p>タグ使用）",
  "key_insight_used": "訴求ポイント"
}}
"""


def generate_email(lead: Lead, service: ServiceType | None = None) -> dict:
    """
    リードに最適化されたメールを生成する
    SNS診断データがある場合は具体的な改善提案メールを、ない場合はベーシック版を生成する

    Returns: {"subject", "body", "body_html", "service_type", "key_insight_used", "has_sns_diagnosis"}
    """
    if service is None:
        service = lead.recommended_service or ServiceType.MIXED

    has_sns_diagnosis = bool(lead.sns_diagnosis and lead.sns_diagnosis.get("diagnosis_summary"))

    if has_sns_diagnosis:
        prompt = _build_prompt_with_sns_diagnosis(lead, service, lead.sns_diagnosis)
        system_msg = (
            "あなたはSNSマーケティングの専門家を兼ねた敏腕営業担当者です。"
            "実際の調査データを活かした具体的・説得力のある提案メールを書きます。"
            "必ずJSON形式のみを出力してください。"
        )
    else:
        prompt = _build_prompt_basic(lead, service)
        system_msg = "あなたは優秀なB2B営業メールライターです。必ずJSON形式で出力してください。"

    raw = _call_ai(system_msg, prompt)
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    result = json.loads(raw)
    return {
        "subject": result.get("subject", ""),
        "body": result.get("body", ""),
        "body_html": result.get("body_html", ""),
        "service_type": service.value if hasattr(service, "value") else str(service),
        "key_insight_used": result.get("key_insight_used", ""),
        "has_sns_diagnosis": has_sns_diagnosis,
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
