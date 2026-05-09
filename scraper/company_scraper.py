"""
企業HPスクレイピング
- SNSリンク検出（Instagram/Twitter/TikTok/YouTube/LINE）
- 電話番号・住所の補完
- 採用情報からマーケティング職募集を検出
"""
import re
import time
import logging
from urllib.parse import urljoin, urlparse
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from config import settings

logger = logging.getLogger(__name__)

SNS_PATTERNS = {
    "instagram": [
        r"instagram\.com/([A-Za-z0-9_.]+)",
        r"instagr\.am/([A-Za-z0-9_.]+)",
    ],
    "twitter": [
        r"twitter\.com/([A-Za-z0-9_]+)",
        r"x\.com/([A-Za-z0-9_]+)",
    ],
    "tiktok": [
        r"tiktok\.com/@([A-Za-z0-9_.]+)",
    ],
    "youtube": [
        r"youtube\.com/(?:channel/|user/|@)([A-Za-z0-9_\-]+)",
        r"youtu\.be/([A-Za-z0-9_\-]+)",
    ],
    "line": [
        r"lin\.ee/([A-Za-z0-9_]+)",
        r"line\.me/R/ti/p/([^\"'\s]+)",
        r"line\.me/en/download",
    ],
}

PHONE_PATTERN = re.compile(
    r"(?:0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}|0\d{9,10})"
)

MARKETER_KEYWORDS = [
    "マーケティング", "SNS担当", "デジタルマーケ", "ウェブ担当",
    "web担当", "EC担当", "広告運用", "インフルエンサー", "コンテンツ",
    "SNS運用", "marketing", "digital"
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get_html(url: str, timeout: int = None) -> Optional[str]:
    timeout = timeout or settings.scrape_timeout_seconds
    try:
        resp = httpx.get(
            url, headers=DEFAULT_HEADERS,
            timeout=timeout, follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"fetch failed {url}: {e}")
        return None


def _extract_sns_links(html: str, base_url: str) -> dict:
    """HTMLからSNSリンクを抽出して各SNSのURLを返す"""
    result = {
        "has_instagram": False, "instagram_url": None,
        "has_twitter": False, "twitter_url": None,
        "has_tiktok": False, "tiktok_url": None,
        "has_youtube": False, "youtube_url": None,
        "has_line_official": False,
    }

    for sns, patterns in SNS_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                full_url = m.group(0)
                if sns == "instagram":
                    result["has_instagram"] = True
                    result["instagram_url"] = f"https://www.{full_url}"
                elif sns == "twitter":
                    result["has_twitter"] = True
                    result["twitter_url"] = f"https://{full_url}"
                elif sns == "tiktok":
                    result["has_tiktok"] = True
                    result["tiktok_url"] = f"https://www.{full_url}"
                elif sns == "youtube":
                    result["has_youtube"] = True
                    result["youtube_url"] = f"https://www.{full_url}"
                elif sns == "line":
                    result["has_line_official"] = True
                break

    return result


def _extract_phone(html: str) -> Optional[str]:
    m = PHONE_PATTERN.search(html)
    return m.group(0).replace(" ", "").replace("　", "") if m else None


def _check_marketer_hiring(html: str, website: str) -> bool:
    """HPの採用ページにマーケター募集があるか確認"""
    text = html.lower()
    for kw in MARKETER_KEYWORDS:
        if kw.lower() in text:
            return True

    # 採用ページへのリンクを探してチェック
    soup = BeautifulSoup(html, "lxml")
    recruit_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text_content = a.get_text().strip()
        if any(kw in text_content for kw in ["採用", "求人", "recruit", "career", "job"]):
            full_url = urljoin(website, href)
            if urlparse(full_url).netloc == urlparse(website).netloc:
                recruit_links.append(full_url)

    for link in recruit_links[:2]:  # 最大2ページまでチェック
        time.sleep(settings.scrape_delay_seconds)
        recruit_html = _get_html(link)
        if recruit_html:
            for kw in MARKETER_KEYWORDS:
                if kw.lower() in recruit_html.lower():
                    return True

    return False


def scrape_company_website(website: str) -> dict:
    """
    企業HPをスクレイピングして情報を返す

    Returns:
        {
            has_instagram, instagram_url, has_twitter, twitter_url,
            has_tiktok, tiktok_url, has_youtube, youtube_url,
            has_line_official, company_phone, is_hiring_marketer,
            runs_web_ads  # Google広告タグ検出
        }
    """
    result = {
        "has_instagram": False, "instagram_url": None,
        "has_twitter": False, "twitter_url": None,
        "has_tiktok": False, "tiktok_url": None,
        "has_youtube": False, "youtube_url": None,
        "has_line_official": False,
        "company_phone": None,
        "is_hiring_marketer": False,
        "runs_web_ads": False,
    }

    if not website:
        return result

    # URLの正規化
    if not website.startswith("http"):
        website = "https://" + website

    html = _get_html(website)
    if not html:
        return result

    # SNS検出
    sns_data = _extract_sns_links(html, website)
    result.update(sns_data)

    # 電話番号
    phone = _extract_phone(html)
    if phone:
        result["company_phone"] = phone

    # Google広告タグ (gtag.js / Google Tag Manager) の検出
    if "gtag(" in html or "googletagmanager" in html or "google_ad_client" in html:
        result["runs_web_ads"] = True

    # 採用ページでマーケター募集チェック
    time.sleep(settings.scrape_delay_seconds)
    result["is_hiring_marketer"] = _check_marketer_hiring(html, website)

    return result


def scrape_and_update_lead(lead) -> dict:
    """
    Leadオブジェクトをスクレイピングして属性を更新する
    Returns: スクレイピング結果dict
    """
    from datetime import datetime
    from database.models import ScrapeStatus

    if not lead.website:
        lead.scrape_status = ScrapeStatus.FAILED
        lead.scrape_error = "URLなし"
        return {}

    lead.scrape_status = ScrapeStatus.RUNNING
    result = {}

    try:
        result = scrape_company_website(lead.website)

        # Leadオブジェクトに反映（既存情報を上書きしない）
        for key, val in result.items():
            if val is None:
                continue
            if key.startswith("has_") and not getattr(lead, key, False):
                setattr(lead, key, val)
            elif key.endswith("_url") and not getattr(lead, key, None):
                setattr(lead, key, val)
            elif key == "company_phone" and not lead.company_phone:
                lead.company_phone = val
            elif key in ("is_hiring_marketer", "runs_web_ads"):
                if val:  # Trueの場合のみ更新
                    setattr(lead, key, val)

        lead.scrape_status = ScrapeStatus.DONE
        lead.scraped_at = datetime.utcnow()

    except Exception as e:
        lead.scrape_status = ScrapeStatus.FAILED
        lead.scrape_error = str(e)
        logger.error(f"scrape failed for {lead.company_name}: {e}")

    return result
