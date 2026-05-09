"""
日本のローカルビジネスを収集してLeadとして登録する
Playwright不要 - httpx + BeautifulSoup + AI生成フォールバック
"""
import json
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
from config import settings

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _scrape_yahoo_local(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """Yahoo!ローカル（local.yahoo.co.jp）から企業情報を収集する"""
    results = []
    query = f"{area} {keyword}"

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        page = 1
        while len(results) < max_results:
            try:
                resp = client.get(
                    "https://local.yahoo.co.jp/search",
                    params={"q": query, "page": str(page)},
                )
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"Yahoo local p{page} error: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            logger.info(f"Yahoo local p{page}: {resp.status_code}, bytes={len(resp.text)}")

            # 複数のセレクタ戦略
            items = (
                soup.select(".elListing")
                or soup.select(".p-listingItem")
                or soup.select("[class*='listing']")
                or soup.select("li[class*='item']")
                or soup.select("article")
            )
            logger.info(f"Yahoo items: {len(items)}")

            if not items:
                # フォールバック: h2/h3のリンクを抽出
                for a in soup.select("h2 a[href*='local.yahoo'], h3 a[href*='local.yahoo']"):
                    name = a.get_text(strip=True)
                    if name and 2 < len(name) < 50:
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
                    or item.select_one("a[href*='/place/']")
                    or item.select_one("strong a, strong")
                )
                company_name = (name_el.get_text(strip=True) if name_el else "").strip()
                if not company_name or len(company_name) < 2:
                    continue

                addr_el = item.select_one("[class*='address'], [class*='addr'], [class*='Address']")
                address = addr_el.get_text(strip=True) if addr_el else None
                if not address:
                    for txt in item.stripped_strings:
                        if re.search(r'(東京|中央区|銀座|丁目|番地)', txt):
                            address = txt
                            break

                text = item.get_text()
                tel_match = re.search(r'0\d[\d\-]{8,11}', text)
                phone = tel_match.group(0) if tel_match else None

                web_el = item.select_one("a[href^='http']:not([href*='yahoo.co.jp'])")
                website = web_el["href"] if web_el else None

                results.append({
                    "company_name": company_name,
                    "address": address,
                    "company_phone": phone,
                    "website": website,
                    "industry": industry,
                    "area": area,
                })
                logger.info(f"Yahoo収集: {company_name} / {address}")

            if len(items) < 5:
                break
            page += 1
            time.sleep(1.0)

    return results[:max_results]


def _scrape_google_local(keyword: str, area: str, industry: str, max_results: int) -> list[dict]:
    """Google検索のローカルパックから店舗情報を収集する"""
    results = []
    query = f"{area} {keyword}"

    with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for start in range(0, min(max_results, 40), 10):
            try:
                resp = client.get(
                    "https://www.google.com/search",
                    params={"q": query, "hl": "ja", "gl": "jp",
                            "num": "10", "start": str(start)},
                )
            except Exception as e:
                logger.warning(f"Google search error: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # ローカルパック（様々なセレクタ）
            items = (
                soup.select(".rllt__details")
                or soup.select(".VkpGBb")
                or soup.select("[data-local-attribute]")
                or soup.select(".lqhpac")
                or soup.select(".uMdZh")
            )

            for item in items:
                name_el = item.select_one("[role='heading'], .dbg0pd, h3, .OSrXXb")
                if not name_el:
                    continue
                company_name = name_el.get_text(strip=True)
                if not company_name:
                    continue

                # 住所をテキストから探す
                address = None
                for el in item.select("div, span"):
                    txt = el.get_text(strip=True)
                    if re.search(r'(東京|中央区|銀座|丁目|番地|〒)', txt):
                        address = txt
                        break

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
                logger.info(f"Google収集: {company_name}")

            if len(results) >= max_results:
                break
            time.sleep(2.0)

    return results[:max_results]


def _generate_with_ai(keyword: str, area: str, industry: str, count: int) -> list[dict]:
    """Gemini AIで代表的な企業リストを生成する（フォールバック）"""
    try:
        if settings.anthropic_api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                system="あなたは日本の地域ビジネス情報に詳しいアシスタントです。必ずJSON配列のみ出力してください。",
                messages=[{"role": "user", "content": _ai_prompt(keyword, area, count)}],
            )
            raw = msg.content[0].text.strip()
        elif settings.gemini_api_key:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=_ai_prompt(keyword, area, count),
            )
            raw = response.text.strip()
        else:
            return []

        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        data = json.loads(raw)
        results = []
        for item in data:
            name = item.get("company_name", "").strip()
            if name:
                results.append({
                    "company_name": name,
                    "address": item.get("address"),
                    "company_phone": item.get("phone"),
                    "website": item.get("website"),
                    "industry": industry,
                    "area": area,
                    "ai_generated": True,
                })
        logger.info(f"AI生成: {len(results)}件")
        return results

    except Exception as e:
        logger.error(f"AI生成エラー: {e}")
        return []


def _ai_prompt(keyword: str, area: str, count: int) -> str:
    return f"""
{area}にある「{keyword}」の店舗・企業を{count}件リストアップしてください。
実際に存在する（または存在する可能性が高い）企業を対象にしてください。
ペットショップ、トリミングサロン、動物病院、ペットホテルなど関連業種を含めてください。

以下のJSON配列のみ出力してください（他のテキストは不要）:
[
  {{
    "company_name": "正式な店舗名",
    "address": "{area}の住所（例: 東京都中央区銀座X-X-X）",
    "phone": "電話番号またはnull",
    "website": "WebサイトURLまたはnull"
  }}
]
"""


async def scrape_google_maps(
    keyword: str,
    area: str,
    industry: str,
    max_results: int = 30,
) -> list[dict]:
    """企業リストを収集する（Yahoo → Google → AI の順で試行）"""
    loop = asyncio.get_event_loop()

    # ① Yahoo!ローカル
    results = await loop.run_in_executor(
        None, lambda: _scrape_yahoo_local(keyword, area, industry, max_results)
    )
    logger.info(f"Yahoo結果: {len(results)}件")

    # ② Google検索
    if not results:
        results = await loop.run_in_executor(
            None, lambda: _scrape_google_local(keyword, area, industry, max_results)
        )
        logger.info(f"Google結果: {len(results)}件")

    # ③ AI生成（フォールバック）
    if not results:
        logger.info("Webスクレイピング0件 → AIで候補リスト生成")
        results = await loop.run_in_executor(
            None, lambda: _generate_with_ai(keyword, area, industry, max_results)
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

        # AI生成の場合はメモに記載
        memo = "※AIが生成した候補リストです。実在確認してください。" if r.get("ai_generated") else None

        lead = Lead(
            company_name=company_name,
            address=r.get("address"),
            company_phone=r.get("company_phone"),
            website=r.get("website"),
            industry=r.get("industry"),
            prefecture=_extract_prefecture(r.get("address", "")),
            source=f"discover:{source_keyword}",
            memo=memo,
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
