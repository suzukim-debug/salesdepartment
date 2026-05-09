"""
Googleマップから企業リストを自動収集してLeadとして登録する
Playwright必須（playwright install chromium）
"""
import logging
import time
import re
from typing import Optional
from sqlalchemy.orm import Session
from database.models import Lead, LeadStatus, ScrapeStatus
from lead_generator.scorer import score_lead
from config import settings

logger = logging.getLogger(__name__)


def _clean_phone(text: str) -> Optional[str]:
    m = re.search(r'[\d\-\(\)\+\s]{10,}', text)
    return m.group(0).strip() if m else None


def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """
    Googleマップで「{area} {keyword}」を検索して企業情報を収集する

    Returns: list of dicts with keys: company_name, address, phone, website, maps_url
    """
    from playwright.sync_api import sync_playwright

    query = f"{area} {keyword}"
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = ctx.new_page()

        try:
            # Googleマップで検索
            search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            page.goto(search_url, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # 検索結果ペインが表示されるまで待つ
            try:
                page.wait_for_selector('[role="feed"]', timeout=10000)
            except Exception:
                logger.warning("検索結果ペインが見つかりません")
                return results

            # スクロールして結果を読み込む
            feed = page.query_selector('[role="feed"]')
            prev_count = 0
            scroll_attempts = 0
            while scroll_attempts < 10:
                items = page.query_selector_all('[role="feed"] > div > div[jsaction]')
                if len(items) >= max_results:
                    break
                if len(items) == prev_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                prev_count = len(items)
                if feed:
                    feed.evaluate("el => el.scrollBy(0, 800)")
                time.sleep(1.5)

            # 各企業の詳細を取得
            items = page.query_selector_all('[role="feed"] > div > div[jsaction]')
            logger.info(f"Found {len(items)} items for '{query}'")

            for item in items[:max_results]:
                try:
                    # 企業名
                    name_el = item.query_selector('[class*="fontHeadlineSmall"]')
                    if not name_el:
                        name_el = item.query_selector('div[aria-label]')
                    company_name = name_el.inner_text().strip() if name_el else None
                    if not company_name:
                        continue

                    # クリックして詳細パネルを開く
                    item.click()
                    time.sleep(2)

                    # 詳細パネルから情報取得
                    address = None
                    phone = None
                    website = None
                    maps_url = page.url

                    # 住所
                    addr_el = page.query_selector('button[data-item-id="address"] .fontBodyMedium')
                    if not addr_el:
                        addr_el = page.query_selector('[data-item-id="address"]')
                    if addr_el:
                        address = addr_el.inner_text().strip()

                    # 電話番号
                    phone_el = page.query_selector('button[data-item-id^="phone"] .fontBodyMedium')
                    if not phone_el:
                        phone_el = page.query_selector('[data-item-id^="phone:tel"]')
                    if phone_el:
                        phone = phone_el.inner_text().strip()

                    # ウェブサイト
                    web_el = page.query_selector('a[data-item-id="authority"]')
                    if web_el:
                        website = web_el.get_attribute("href")

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
            browser.close()

    return results


def import_maps_results(
    db: Session,
    results: list[dict],
    source_keyword: str,
) -> dict:
    """
    Googleマップ収集結果をLeadとしてDBに保存する（重複スキップ）

    Returns: {"created": int, "skipped": int}
    """
    created = 0
    skipped = 0

    for r in results:
        company_name = r.get("company_name", "").strip()
        if not company_name:
            continue

        # 重複チェック（会社名で判定）
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
