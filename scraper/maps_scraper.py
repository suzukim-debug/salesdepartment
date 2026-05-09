"""
日本のローカルビジネスディレクトリから企業リストを自動収集してLeadとして登録する
httpx + BeautifulSoup のみ使用（Playwright不要）
対応サイト: ekiten.jp → itp.ne.jp → フォールバック
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


def _scrape_ekiten(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """ekiten.jp（エキテン）から企業情報を収集する"""
    results = []

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        page = 1
        while len(results) < max_results:
            try:
                url = "https://www.ekiten.jp/search/"
                params = {"q": keyword, "area_name": area, "page": str(page)}
                resp = client.get(url, params=params)
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"ekiten fetch error p{page}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.find("title")
            logger.info(f"ekiten page{page} title: {title.get_text(strip=True) if title else 'N/A'}")

            # エキテンの店舗リスト
            items = (
                soup.select(".p-shop-list__item")
                or soup.select(".shop-list-item")
                or soup.select("li[class*='shop']")
                or soup.select("article[class*='shop']")
                or soup.select(".c-card")
            )

            logger.info(f"ekiten items found: {len(items)}")

            if not items:
                # ページ構造が変わっている場合の汎用セレクタ
                items = soup.select("h2 a, h3 a")
                for link in items[:max_results]:
                    name = link.get_text(strip=True)
                    if name and 3 < len(name) < 50:
                        results.append({
                            "company_name": name,
                            "address": None,
                            "company_phone": None,
                            "website": None,
                            "industry": industry,
                            "area": area,
                        })
                break

            for item in items:
                name_el = (
                    item.select_one("h2 a, h3 a, h4 a")
                    or item.select_one("[class*='name'] a, [class*='Name'] a")
                    or item.select_one("a[href*='/shop/']")
                )
                company_name = (name_el.get_text(strip=True) if name_el else "").strip()
                if not company_name or len(company_name) < 2:
                    continue

                addr_el = item.select_one("[class*='address'], [class*='addr'], [class*='Address']")
                address = addr_el.get_text(strip=True) if addr_el else None

                # 電話番号をテキストから抽出
                text = item.get_text()
                tel_match = re.search(r'0\d[\d\-]{8,11}', text)
                phone = tel_match.group(0) if tel_match else None

                results.append({
                    "company_name": company_name,
                    "address": address,
                    "company_phone": phone,
                    "website": None,
                    "industry": industry,
                    "area": area,
                })
                logger.info(f"ekiten収集: {company_name} / {address}")

            if len(items) < 5:
                break
            page += 1
            time.sleep(1.0)

    return results[:max_results]


def _scrape_itp(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """itp.ne.jp（NTTタウンページ）から企業情報を収集するフォールバック"""
    results = []
    query = f"{area} {keyword}"
    start = 1

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        while len(results) < max_results:
            try:
                resp = client.get(
                    "https://itp.ne.jp/service/SS0001/",
                    params={"sKey": query, "sStartCount": str(start)},
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"itp fetch error: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            title = soup.find("title")
            logger.info(f"itp title: {title.get_text(strip=True) if title else 'N/A'}, bytes={len(resp.text)}")

            items = (
                soup.select(".shopDataBox")
                or soup.select(".p-shopItem")
                or soup.select("article.shop")
                or soup.select("[class*='shopItem']")
            )
            logger.info(f"itp items found: {len(items)}")
            if not items:
                break

            for item in items:
                name_el = (
                    item.select_one("h3 a, h2 a")
                    or item.select_one("[class*='shopName']")
                    or item.select_one("[class*='Name']")
                )
                company_name = (name_el.get_text(strip=True) if name_el else "").strip()
                if not company_name:
                    continue

                addr_el = item.select_one("[class*='address'], [class*='addr']")
                address = addr_el.get_text(strip=True) if addr_el else None

                text = item.get_text()
                tel_match = re.search(r'0\d[\d\-]{8,11}', text)
                phone = tel_match.group(0) if tel_match else None

                results.append({
                    "company_name": company_name,
                    "address": address,
                    "company_phone": phone,
                    "website": None,
                    "industry": industry,
                    "area": area,
                })
                logger.info(f"itp収集: {company_name}")

            if len(items) < 10:
                break
            start += 10
            time.sleep(1.0)

    return results[:max_results]


async def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """企業リストを収集する（ekiten.jp → itp.ne.jp の順で試行）"""
    loop = asyncio.get_event_loop()

    # まずエキテン
    results = await loop.run_in_executor(
        None,
        lambda: _scrape_ekiten(keyword, area, industry, max_results),
    )
    logger.info(f"ekiten結果: {len(results)}件")

    # エキテンで取れなければitp
    if not results:
        results = await loop.run_in_executor(
            None,
            lambda: _scrape_itp(keyword, area, industry, max_results),
        )
        logger.info(f"itp結果: {len(results)}件")

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
