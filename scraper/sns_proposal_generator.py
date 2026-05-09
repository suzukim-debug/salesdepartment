"""
Claude APIを使ってSNSスクレイピングデータを分析し
企業ごとの具体的な改善施策を含む「SNS診断レポート」を生成する
"""
import json
import logging
import anthropic
from config import settings

logger = logging.getLogger(__name__)


def _build_analysis_prompt(company_name: str, industry: str, sns_data: dict) -> str:
    """ClaudeへのSNS診断プロンプトを構築する"""

    sections = []

    ig = sns_data.get("instagram")
    if ig and not ig.get("error"):
        sections.append(f"""
【Instagram】
- フォロワー数: {ig.get('followers', '不明')}人
- 投稿数: {ig.get('post_count', '不明')}件
- 投稿頻度: {ig.get('post_frequency_hint', '不明')}
- 最終投稿: {ig.get('last_post_hint', '不明')}
- エンゲージメント率: {ig.get('engagement_rate_estimate', '不明')}%
- 平均いいね数: {ig.get('avg_likes_estimate', '不明')}
- プロフィール文: {ig.get('bio', '不明')[:100] if ig.get('bio') else '不明'}
- コンテンツテーマ: {', '.join(ig.get('content_themes', [])) or '不明'}
- 使用ハッシュタグ例: {', '.join(ig.get('hashtags_used', [])[:5]) or 'なし'}
- ビジネスアカウント: {'はい' if ig.get('is_business') else 'いいえ'}
- 検出された問題: {', '.join(ig.get('issues_found', [])) or 'なし'}
- 強み: {', '.join(ig.get('strengths', [])) or 'なし'}
""".strip())

    tt = sns_data.get("tiktok")
    if tt and not tt.get("error"):
        sections.append(f"""
【TikTok】
- フォロワー数: {tt.get('followers', '不明')}人
- 総いいね数: {tt.get('total_likes', '不明')}
- 動画投稿数: {tt.get('video_count', '不明')}本
- 検出された問題: {', '.join(tt.get('issues_found', [])) or 'なし'}
- 強み: {', '.join(tt.get('strengths', [])) or 'なし'}
""".strip())

    yt = sns_data.get("youtube")
    if yt and not yt.get("error"):
        recent = yt.get("recent_videos", [])[:3]
        video_titles = "\n  ".join([f"・{v['title']} ({v.get('date', '')})" for v in recent]) if recent else "取得不可"
        sections.append(f"""
【YouTube】
- チャンネル登録者数: {yt.get('subscribers', '不明')}
- 動画数: {yt.get('video_count', '不明')}
- 投稿頻度: {yt.get('upload_frequency_hint', '不明')}
- 平均再生数: {yt.get('avg_views_per_video', '不明')}回
- 最近の動画:
  {video_titles}
- 検出された問題: {', '.join(yt.get('issues_found', [])) or 'なし'}
""".strip())

    tw = sns_data.get("twitter")
    if tw and not tw.get("error"):
        sections.append(f"""
【X(Twitter)】
- フォロワー数: {tw.get('followers', '不明')}人
- 検出された問題: {', '.join(tw.get('issues_found', [])) or 'なし'}
""".strip())

    summary = sns_data.get("summary", {})

    sns_section = "\n\n".join(sections) if sections else "SNSアカウントなし（または取得不可）"

    return f"""あなたはSNSマーケティングの専門コンサルタントです。
以下の企業のSNS運用状況を分析し、具体的な改善施策を提案する診断レポートを作成してください。

【企業情報】
- 会社名: {company_name}
- 業種: {industry}

【SNS実態データ（実際のページを解析した結果）】
{sns_section}

【総合評価】
- 分析できたプラットフォーム数: {summary.get('platforms_analyzed', 0)}
- 検出された問題の総数: {summary.get('total_issues', 0)}
- 総合アセスメント: {summary.get('overall_assessment', '不明')}

以下のJSON形式で診断レポートを出力してください:

{{
  "diagnosis_summary": "2〜3文で現状の総括（具体的な数字を必ず含める）",
  "critical_issues": [
    {{
      "platform": "プラットフォーム名",
      "issue": "具体的な問題（数字入り）",
      "impact": "ビジネスへの影響",
      "severity": "high/medium/low"
    }}
  ],
  "improvement_proposals": [
    {{
      "title": "施策名（端的に）",
      "platform": "対象SNS",
      "detail": "具体的な施策内容（何をどう変えるか）",
      "expected_result": "期待できる数値効果（例：フォロワー月+300人、エンゲージメント率2倍）",
      "timeline": "効果が出るまでの期間",
      "difficulty": "easy/medium/hard"
    }}
  ],
  "quick_wins": ["すぐ（1週間以内）に実施できる改善策を3つ"],
  "competitor_gap": "同業他社との差を1文で表現",
  "overall_score": 0〜100の整数（現状のSNS運用スコア）,
  "potential_score": 0〜100の整数（適切な施策を実施した場合の達成可能スコア）
}}

厳密にJSON形式のみ出力してください。"""


def _call_ai(prompt: str) -> str:
    """Claude または Gemini でテキスト生成"""
    system_msg = (
        "あなたはSNSマーケティングの専門コンサルタントです。"
        "実データに基づいた具体的な診断と施策提案を行います。"
        "必ず有効なJSONのみを出力してください。"
    )
    if settings.anthropic_api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_msg,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    elif settings.gemini_api_key:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-1.5-flash-latest",
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_msg),
        )
        return response.text.strip()
    else:
        raise ValueError("ANTHROPIC_API_KEY または GEMINI_API_KEY を設定してください")


def generate_sns_diagnosis(company_name: str, industry: str, sns_data: dict) -> dict:
    """
    AI APIを使ってSNS診断レポートを生成する（Claude / Gemini 自動切替）
    """
    prompt = _build_analysis_prompt(company_name, industry, sns_data)

    try:
        raw = _call_ai(prompt)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        diagnosis = json.loads(raw)
        diagnosis["_generated_at"] = __import__("datetime").datetime.utcnow().isoformat()
        return diagnosis

    except Exception as e:
        logger.error(f"SNS diagnosis generation failed: {e}")
        return {
            "diagnosis_summary": "診断生成に失敗しました",
            "critical_issues": [],
            "improvement_proposals": [],
            "quick_wins": [],
            "competitor_gap": "",
            "overall_score": 0,
            "potential_score": 0,
            "error": str(e),
        }


def run_full_sns_analysis(lead, db=None) -> dict:
    """
    SNSスクレイピング → Claude診断 を一気通貫で実行する

    Returns: {sns_raw_data, diagnosis}
    """
    from scraper.sns_deep_scraper import deep_scrape_all_sns

    logger.info(f"SNS深掘り分析開始: {lead.company_name}")

    # 1. 実際にSNSを訪問してデータ収集
    sns_raw = deep_scrape_all_sns(lead)

    # 2. Claude でSNS診断レポートを生成
    diagnosis = generate_sns_diagnosis(
        lead.company_name,
        lead.industry or "不明",
        sns_raw,
    )

    # 3. DBに保存
    if db is not None:
        lead.sns_raw_data = sns_raw
        lead.sns_diagnosis = diagnosis
        # SNSフォロワー概算をLeadに反映
        ig = sns_raw.get("instagram", {})
        if ig and ig.get("followers") and not lead.sns_follower_estimate:
            from scraper.sns_analyzer import _format_follower_count
            lead.sns_follower_estimate = _format_follower_count(ig["followers"])

    logger.info(f"SNS診断完了: {lead.company_name} スコア={diagnosis.get('overall_score')}")
    return {"sns_raw_data": sns_raw, "diagnosis": diagnosis}
