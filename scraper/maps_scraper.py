"""
NTTタウンページ（itp.ne.jp）から企業リストを自動収集してLeadとして登録する
Playwright不要 - httpx + BeautifulSoup のみ使用
"""
import logging
import asyncio
import time
import re
from typing import Optional
from sqlalchemy.orm import Session
import httpx
from bs4 import BeautifulSoup
from database.models import Lead, LeadStatus, ScrapeStatus
from lead_generator.scorer import score_lead

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _scrape_itp(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """itp.ne.jp（NTTタウンページ）から企業情報を収集する"""
    results = []
    query = f"{area} {keyword}"
    start = 1

    with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        while len(results) < max_results:
            try:
                resp = client.get(
                    "https://itp.ne.jp/service/SS0001/",
                    params={"sKey": query, "sStartCount": str(start)},
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"itp.ne.jp fetch error: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # 企業リストを抽出（複数セレクタ対応）
            items = (
                soup.select(".shopDataBox")
                or soup.select(".p-shopItem")
                or soup.select("article.shop")
                or soup.select("[class*='shopItem']")
                or soup.select("[class*='Shop']")
            )

            if not items:
                logger.warning(f"itp.ne.jp: セレクタが合いません。page={start}")
                # デバッグ: ページタイトルを確認
                title = soup.find("title")
                logger.info(f"Page title: {title.get_text() if title else 'N/A'}")
                break

            for item in items:
                # 企業名
                name_el = (
                    item.select_one("h3")
                    or item.select_one("h2")
                    or item.select_one("[class*='shopName']")
                    or item.select_one("[class*='Name']")
                    or item.select_one("a[href*='/detail/']")
                )
                company_name = name_el.get_text(strip=True) if name_el else ""
                if not company_name:
                    continue

                # 住所
                addr_el = (
                    item.select_one("[class*='address']")
                    or item.select_one("[class*='Address']")
                    or item.select_one("[class*='addr']")
                )
                address = addr_el.get_text(strip=True) if addr_el else None

                # 電話番号
                tel_el = (
                    item.select_one("[class*='tel']")
                    or item.select_one("[class*='Tel']")
                    or item.select_one("[class*='phone']")
                )
                phone = tel_el.get_text(strip=True) if tel_el else None
                if not phone:
                    # テキストから電話番号パターンを探す
                    m = re.search(r'0\d[\d\-]{8,11}', item.get_text())
                    phone = m.group(0) if m else None

                # ウェブサイト
                web_el = item.select_one("a[href^='http']:not([href*='itp.ne.jp'])")
                website = web_el["href"] if web_el else None

                results.append({
                    "company_name": company_name,
                    "address": address,
                    "company_phone": phone,
                    "website": website,
                    "industry": industry,
                    "area": area,
                })
                logger.info(f"収集: {company_name} / {address} / {phone}")

            if len(items) < 10:
                break  # 最終ページ
            start += 10
            time.sleep(1.0)

    return results[:max_results]


def _scrape_google_search(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """Google検索結果から店舗情報を収集するフォールバック"""
    results = []
    query = f"{area} {keyword} 店舗一覧"

    with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for page in range(0, min(3, max_results // 5 + 1)):
            try:
                resp = client.get(
                    "https://www.google.com/search",
                    params={"q": query, "start": str(page * 10), "hl": "ja"},
                )
                soup = BeautifulSoup(resp.text, "lxml")

                # ローカルパック（Googleマップ掲載情報）
                local_results = soup.select("div[data-local-attribute], .rllt__details, .VkpGBb")
                for item in local_results:
                    name_el = item.select_one("[role='heading'], h3, .dbg0pd")
                    if name_el:
                        addr_el = item.select_one(".rllt__details div:nth-child(2), .LrzXr")
                        results.append({
                            "company_name": name_el.get_text(strip=True),
                            "address": addr_el.get_text(strip=True) if addr_el else None,
                            "company_phone": None,
                            "website": None,
                            "industry": industry,
                            "area": area,
                        })

                if len(results) >= max_results:
                    break
                time.sleep(2.0)
            except Exception as e:
                logger.error(f"Google search error: {e}")
                break

    return results[:max_results]


async def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """企業リストを収集する（itp.ne.jp → Googleフォールバック）"""
    loop = asyncio.get_event_loop()

    # itp.ne.jp で収集
    results = await loop.run_in_executor(
        None,
        lambda: _scrape_itp(keyword, area, industry, max_results),
    )

    # itp.ne.jp で0件だったらGoogleで補完
    if not results:
        logger.info("itp.ne.jpで0件 → Google検索フォールバック")
        results = await loop.run_in_executor(
            None,
            lambda: _scrape_google_search(keyword, area, industry, max_results),
        )

    return results


def import_maps_results(
    db: Session,
    results: list[dict],
    source_keyword: str,
) -> dict:
    """収集結果をLeadとしてDBに保存（重複スキップ）"""
    created = 0
    skipped = 0

    for r in results:
        company_name = r.get("company_name", "").strip()
        if not company_name:
            continue

        exists = db.query(Lead).filter(Lead.company_name == company_name).first()
        if exists:
            skipped += 1
            continue

        lead = Lead(
            company_name=company_name,
            address=r.get("address"),
            company_phone=r.get("company_phone"),
            website=r.get("website"),
            industry=r.get("industry"),
            prefecture=_extract_prefecture(r.get("address", "")),
            source=f"discover:{source_keyword}",
            status=LeadStatus.NEW,
            scrape_status=ScrapeStatus.PENDING,
        )

        score, service = score_lead(lead)
        lead.lead_score = score
        lead.recommended_service = service

        db.add(lead)
        created += 1

    db.commit()
    return {"created": created, "skipped": skipped}


def _extract_prefecture(address: str) -> Optional[str]:
    if not address:
        return None
    prefs = [
        "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
        "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
        "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
        "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
        "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
        "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
        "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
    ]
    for p in prefs:
        if p in address:
            return p
    if "東京" in address or "銀座" in address or "渋谷" in address:
        return "東京都"
    return None
