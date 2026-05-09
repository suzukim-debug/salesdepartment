"""
SNSアクティビティ分析
Instagramのフォロワー概算・最終投稿日を推定する（公開情報のみ）
"""
import re
import time
import logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from config import settings

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja,en;q=0.9",
}


def _fetch(url: str) -> Optional[str]:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=settings.scrape_timeout_seconds,
                         follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.warning(f"SNS fetch failed {url}: {e}")
    return None


def analyze_instagram(username_or_url: str) -> dict:
    """
    Instagramプロフィールページから公開情報を取得
    Returns: {follower_estimate, post_count, is_active, bio_snippet}
    """
    result = {
        "follower_estimate": "不明",
        "post_count": 0,
        "is_active": False,
        "bio_snippet": "",
    }

    # URL からユーザー名を抽出
    username = username_or_url
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", username_or_url)
    if m:
        username = m.group(1)

    username = username.strip("/").split("?")[0]
    if not username:
        return result

    html = _fetch(f"https://www.instagram.com/{username}/")
    if not html:
        return result

    # meta タグからフォロワー数を取得
    follower_m = re.search(
        r'"edge_followed_by":\{"count":(\d+)\}',
        html
    )
    if follower_m:
        count = int(follower_m.group(1))
        result["follower_estimate"] = _format_follower_count(count)
        result["is_active"] = True

    # OGPタグから情報取得 (フォールバック)
    if not follower_m:
        soup = BeautifulSoup(html, "lxml")
        desc = soup.find("meta", {"name": "description"})
        if desc and desc.get("content"):
            content = desc["content"]
            # "1,234 Followers, 567 Following, 89 Posts"
            m2 = re.search(r"([\d,]+)\s*(?:Followers|フォロワー)", content)
            if m2:
                count_str = m2.group(1).replace(",", "")
                result["follower_estimate"] = _format_follower_count(int(count_str))
                result["is_active"] = True

    return result


def _format_follower_count(n: int) -> str:
    if n >= 100000:
        return f"{n // 10000}万以上"
    elif n >= 10000:
        return f"{n // 1000}千〜{(n // 1000) + 1}千"
    elif n >= 1000:
        return f"{n // 1000}千"
    elif n >= 100:
        return f"{n // 100}百"
    else:
        return f"{n}未満1千"


def analyze_sns_activity(lead) -> dict:
    """
    Leadオブジェクトの各SNSアクティビティを分析する
    Returns: {sns_post_frequency, sns_follower_estimate, sns_activity_score}
    """
    result = {
        "sns_post_frequency": lead.sns_post_frequency or "不明",
        "sns_follower_estimate": lead.sns_follower_estimate or "不明",
        "sns_activity_score": 0,
    }

    if lead.instagram_url:
        time.sleep(settings.scrape_delay_seconds)
        ig_data = analyze_instagram(lead.instagram_url)
        if ig_data["follower_estimate"] != "不明":
            result["sns_follower_estimate"] = ig_data["follower_estimate"]
            result["sns_activity_score"] += 10
            if ig_data["is_active"]:
                result["sns_activity_score"] += 5

    # フォロワー規模からスコア加算
    est = result["sns_follower_estimate"]
    if "万以上" in est:
        result["sns_activity_score"] += 20
    elif "千" in est:
        result["sns_activity_score"] += 10
    elif "百" in est:
        result["sns_activity_score"] += 5

    return result
