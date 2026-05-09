"""
Googleマップから企業リストを自動収集してLeadとして登録する
Windows ProactorEventLoop対応版 - Playwrightを別スレッドで実行
"""
import sys
import logging
import asyncio
import traceback
import concurrent.futures
from typing import Optional
from sqlalchemy.orm import Session
from database.models import Lead, LeadStatus, ScrapeStatus
from lead_generator.scorer import score_lead

logger = logging.getLogger(__name__)


async def _scrape_async(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """Playwright async スクレイピング本体"""
    from playwright.async_api import async_playwright

    query = f"{area} {keyword}"
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )

        try:
            search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            logger.info(f"Navigating: {search_url}")
            await page.goto(search_url, timeout=30000)
            await asyncio.sleep(3)

            # Cookie同意ダイアログを閉じる
            for selector in [
                'button:has-text("すべて同意")',
                'button:has-text("Accept all")',
                'button[aria-label*="同意"]',
                'form[action*="consent"] button',
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await asyncio.sleep(2)
                        break
                except Exception:
                    pass

            # フィードを待つ
            try:
                await page.locator('[role="feed"]').wait_for(timeout=12000)
            except Exception:
                logger.warning("フィードが見つかりません")
                logger.info(f"Page title: {await page.title()}")
                return results

            # スクロールして件数を増やす
            feed = page.locator('[role="feed"]')
            for _ in range(6):
                cnt = await page.locator('[role="feed"] a[href*="/maps/place"]').count()
                logger.info(f"件数: {cnt}")
                if cnt >= max_results:
                    break
                await feed.evaluate("el => el.scrollBy(0, 1000)")
                await asyncio.sleep(1.5)

            # aria-label付きリンクで企業名を取得
            links = await page.locator('[role="feed"] a[href*="/maps/place"][aria-label]').all()
            logger.info(f"リンク数: {len(links)}")

            for i, link in enumerate(links[:max_results]):
                try:
                    company_name = (await link.get_attribute("aria-label") or "").strip()
                    if not company_name:
                        continue

                    await link.click()
                    await asyncio.sleep(2)

                    address = phone = website = None
                    maps_url = page.url

                    try:
                        addr = page.locator('[data-item-id="address"] .fontBodyMedium, button[data-item-id="address"]').first
                        address = (await addr.text_content(timeout=3000) or "").strip() or None
                    except Exception:
                        pass

                    try:
                        for tel_sel in [
                            '[data-item-id^="phone:tel"] .fontBodyMedium',
                            '[data-tooltip="電話番号をコピー"] .fontBodyMedium',
                        ]:
                            tel = page.locator(tel_sel).first
                            phone = (await tel.text_content(timeout=2000) or "").strip() or None
                            if phone:
                                break
                    except Exception:
                        pass

                    try:
                        web = page.locator('a[data-item-id="authority"]').first
                        website = await web.get_attribute("href", timeout=2000)
                    except Exception:
                        pass

                    results.append({
                        "company_name": company_name,
                        "address": address,
                        "company_phone": phone,
                        "website": website,
                        "maps_url": maps_url,
                        "industry": industry,
                        "area": area,
                    })
                    logger.info(f"[{i+1}] {company_name} / {address}")

                except Exception as e:
                    logger.warning(f"item[{i}] error: {e}")
                    continue

        finally:
            await browser.close()

    return results


def _run_in_new_loop(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """Windowsの ProactorEventLoop で Playwright を動かす（別スレッド用）"""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scrape_async(keyword, area, industry, max_results))
    except Exception as e:
        logger.error(f"scrape error in thread: {e}\n{traceback.format_exc()}")
        raise
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """FastAPI async context から呼ぶ。Playwrightは別スレッドの独立したループで実行。"""
    event_loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        results = await event_loop.run_in_executor(
            executor,
            lambda: _run_in_new_loop(keyword, area, industry, max_results),
        )
    return results


def import_maps_results(
    db: Session,
    results: list[dict],
    source_keyword: str,
) -> dict:
    """Googleマップ収集結果をLeadとしてDBに保存（重複スキップ）"""
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
