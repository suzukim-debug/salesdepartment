"""
求人サイトスクレイピング
IndeedJapan / Wantedly / 求人ボックス から
マーケティング職の求人を検索し、予算規模・デジタル投資意欲を推定する
"""
import re
import time
import logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from config import settings

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}

# 予算があることを示すキーワード
BUDGET_KEYWORDS_HIGH = [
    "予算無制限", "予算潤沢", "年商10億", "年商50億", "年商100億",
    "上場企業", "東証", "マザーズ", "グロース市場",
    "SNS広告費", "広告予算", "デジタルマーケ予算",
]

BUDGET_KEYWORDS_MID = [
    "SNS運用", "インフルエンサー", "動画広告", "YouTube広告",
    "Instagram広告", "TikTok広告", "デジタルマーケ",
    "WEB広告", "リスティング", "ディスプレイ広告",
]

SALARY_PATTERN = re.compile(
    r"(?:年収|月給|年俸)\s*[：:]\s*(\d+(?:\.\d+)?)\s*(?:万|千万|億)"
)


def _fetch(url: str) -> Optional[str]:
    try:
        resp = httpx.get(
            url, headers=DEFAULT_HEADERS,
            timeout=settings.scrape_timeout_seconds,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"fetch failed {url}: {e}")
        return None


def search_indeed(company_name: str, prefecture: str = "") -> dict:
    """
    Indeed Japanで企業のマーケティング職求人を検索する
    Returns: {found: bool, job_titles: list, budget_signals: list, salary_max: int}
    """
    query = f"{company_name} マーケティング SNS"
    loc = prefecture or "東京都"
    url = f"https://jp.indeed.com/jobs?q={query}&l={loc}"

    result = {
        "found": False, "job_titles": [],
        "budget_signals": [], "salary_max": 0,
    }

    html = _fetch(url)
    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    # 求人タイトルを抽出
    for card in soup.select("[data-jk], .jobsearch-SerpJobCard, .tapItem")[:10]:
        title_el = card.select_one("h2, .jobTitle, .title")
        if title_el:
            title = title_el.get_text(strip=True)
            # 会社名がタイトルや周辺テキストに含まれるか確認
            card_text = card.get_text()
            if company_name[:4] in card_text:  # 会社名の最初の4文字でマッチ
                result["found"] = True
                result["job_titles"].append(title)

                # 予算シグナル検出
                for kw in BUDGET_KEYWORDS_HIGH:
                    if kw in card_text:
                        result["budget_signals"].append(f"HIGH:{kw}")
                for kw in BUDGET_KEYWORDS_MID:
                    if kw in card_text:
                        result["budget_signals"].append(f"MID:{kw}")

                # 給与情報から予算規模を推定
                m = SALARY_PATTERN.search(card_text)
                if m:
                    try:
                        salary = float(m.group(1))
                        unit = m.group(0)
                        if "億" in unit:
                            salary *= 10000
                        elif "千万" in unit:
                            salary *= 1000
                        result["salary_max"] = max(result["salary_max"], int(salary))
                    except ValueError:
                        pass

    return result


def search_wantedly(company_name: str) -> dict:
    """
    Wantedlyで企業のデジタルマーケ求人を検索する
    """
    url = f"https://www.wantedly.com/projects?q={company_name}+マーケティング&type=full_time"
    result = {"found": False, "job_titles": [], "tech_signals": []}

    html = _fetch(url)
    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    for card in soup.select(".ProjectCard, [data-testid='project-card'], article")[:10]:
        text = card.get_text()
        if company_name[:4] in text:
            result["found"] = True
            title_el = card.select_one("h2, h3, .title")
            if title_el:
                result["job_titles"].append(title_el.get_text(strip=True))

            for kw in ["Instagram", "TikTok", "YouTube", "インフルエンサー", "SNS広告"]:
                if kw.lower() in text.lower():
                    result["tech_signals"].append(kw)

    return result


def estimate_budget_from_jobs(company_name: str, prefecture: str = "") -> dict:
    """
    求人情報から予算規模を総合的に推定する
    Returns:
        {
            budget_estimate: "高（月30万以上）" | "中（月10〜30万）" | "低（〜月10万）" | "不明",
            is_hiring_marketer: bool,
            job_signals: list[str],
        }
    """
    signals = []
    is_hiring = False

    time.sleep(settings.scrape_delay_seconds)
    indeed = search_indeed(company_name, prefecture)
    if indeed["found"]:
        is_hiring = True
        signals.extend(indeed["budget_signals"])
        signals.extend([f"Indeed求人: {t}" for t in indeed["job_titles"][:2]])

    time.sleep(settings.scrape_delay_seconds)
    wantedly = search_wantedly(company_name)
    if wantedly["found"]:
        is_hiring = True
        signals.extend([f"Wantedly: {t}" for t in wantedly["job_titles"][:2]])
        signals.extend(wantedly["tech_signals"])

    # 予算推定ロジック
    high_count = sum(1 for s in signals if "HIGH:" in s)
    mid_count = sum(1 for s in signals if "MID:" in s or "Wantedly" in s)

    if high_count >= 1 or (indeed.get("salary_max", 0) >= 700):
        budget = "高（月30万以上）"
    elif mid_count >= 2 or is_hiring:
        budget = "中（月10〜30万）"
    elif is_hiring:
        budget = "低〜中（月10〜30万）"
    else:
        budget = "不明"

    return {
        "budget_estimate": budget,
        "is_hiring_marketer": is_hiring,
        "job_signals": signals[:10],
    }
