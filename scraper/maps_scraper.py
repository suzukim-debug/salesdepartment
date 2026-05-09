"""
Googleマップから企業リストを自動収集してLeadとして登録する
Playwright async API使用（playwright install chromium）
"""
import logging
import asyncio
import re
from typing import Optional
from sqlalchemy.orm import Session
from database.models import Lead, LeadStatus, ScrapeStatus
from lead_generator.scorer import score_lead

logger = logging.getLogger(__name__)


async def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """
    Googleマップで「{area} {keyword}」を検索して企業情報を収集する（async版）
    """
    from playwright.async_api import async_playwright

    query = f"{area} {keyword}"
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = await ctx.new_page()

        try:
            search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            try:
                await page.wait_for_selector('[role="feed"]', timeout=10000)
            except Exception:
                logger.warning("検索結果ペインが見つかりません")
                return results

            # スクロールして結果を読み込む
            prev_count = 0
            scroll_attempts = 0
            while scroll_attempts < 10:
                items = await page.query_selector_all('[role="feed"] > div > div[jsaction]')
                if len(items) >= max_results:
                    break
                if len(items) == prev_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                prev_count = len(items)
                feed = await page.query_selector('[role="feed"]')
                if feed:
                    await feed.evaluate("el => el.scrollBy(0, 800)")
                await asyncio.sleep(1.5)

            items = await page.query_selector_all('[role="feed"] > div > div[jsaction]')
            logger.info(f"Found {len(items)} items for '{query}'")

            for item in items[:max_results]:
                try:
                    name_el = await item.query_selector('[class*="fontHeadlineSmall"]')
                    if not name_el:
                        name_el = await item.query_selector('div[aria-label]')
                    company_name = (await name_el.inner_text()).strip() if name_el else None
                    if not company_name:
                        continue

                    await item.click()
                    await asyncio.sleep(2)

                    maps_url = page.url
                    address = phone = website = None

                    addr_el = await page.query_selector('button[data-item-id="address"] .fontBodyMedium')
                    if not addr_el:
                        addr_el = await page.query_selector('[data-item-id="address"]')
                    if addr_el:
                        address = (await addr_el.inner_text()).strip()

                    phone_el = await page.query_selector('button[data-item-id^="phone"] .fontBodyMedium')
                    if not phone_el:
                        phone_el = await page.query_selector('[data-item-id^="phone:tel"]')
                    if phone_el:
                        phone = (await phone_el.inner_text()).strip()

                    web_el = await page.query_selector('a[data-item-id="authority"]')
                    if web_el:
                        website = await web_el.get_attribute("href")

                    results.append({
                        "company_name": company_name,
                        "address": address,
                        "company_phone": phone,
                        "website": website,
                        "maps_url": maps_url,
                        "industry": industry,
                        "area": area,
                    })
                    logger.info(f"収集: {company_name} / {address} / {website}")

                except Exception as e:
                    logger.warning(f"item parse error: {e}")
                    continue

        except Exception as e:
            logger.error(f"maps scrape failed: {e}")
        finally:
            await browser.close()

    return results


def import_maps_results(
    db: Session,
    results: list[dict],
    source_keyword: str,
) -> dict:
    """
    Googleマップ収集結果をLeadとしてDBに保存する（重複スキップ）
    """
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
            source=f"googlemap:{source_keyword}",
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
