"""
SNSページ深掘りスクレイパー
Instagram / TikTok / YouTube / Twitter(X) の公開ページに直接アクセスして
投稿頻度・エンゲージメント・コンテンツ傾向などの実データを取得する
"""
import re
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlencode
import httpx
from bs4 import BeautifulSoup
from config import settings

logger = logging.getLogger(__name__)

# ─── 共通 ────────────────────────────────────────────────────

def _headers(mobile: bool = False) -> dict:
    if mobile:
        return {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _fetch(url: str, mobile: bool = False, timeout: int = None) -> Optional[str]:
    timeout = timeout or settings.scrape_timeout_seconds
    try:
        resp = httpx.get(
            url, headers=_headers(mobile),
            timeout=timeout, follow_redirects=True,
        )
        if resp.status_code == 200:
            return resp.text
        logger.debug(f"HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.warning(f"fetch failed [{url}]: {e}")
    return None


def _extract_username(url: str, domain: str) -> Optional[str]:
    """URL からユーザー名を抽出"""
    m = re.search(rf"{re.escape(domain)}/([A-Za-z0-9_.@\-]+)", url)
    if m:
        name = m.group(1).lstrip("@").strip("/").split("?")[0]
        if name and name not in {"explore", "reel", "p", "tv", "stories", "about", "channel"}:
            return name
    return None


# ─── Instagram ───────────────────────────────────────────────

def scrape_instagram(url_or_username: str) -> dict:
    """
    Instagram公開プロフィールを解析する

    Returns:
        platform, username, followers, following, post_count,
        bio, is_business, last_post_hint, content_themes,
        hashtags, avg_likes_estimate, issues_found, raw_html_snippet
    """
    result = {
        "platform": "instagram",
        "username": None,
        "profile_url": url_or_username,
        "followers": None,
        "following": None,
        "post_count": None,
        "bio": None,
        "is_business": False,
        "post_frequency_hint": None,   # 例: "週1〜2回"
        "last_post_hint": None,        # 例: "3週間以上前"
        "content_themes": [],          # 例: ["商品紹介", "スタッフ紹介"]
        "hashtags_used": [],
        "avg_likes_estimate": None,
        "engagement_rate_estimate": None,
        "issues_found": [],            # 診断で見つかった問題
        "strengths": [],               # 強み
        "error": None,
    }

    username = _extract_username(url_or_username, "instagram.com") or url_or_username.strip("/")
    if not username:
        result["error"] = "ユーザー名を取得できません"
        return result

    result["username"] = username
    result["profile_url"] = f"https://www.instagram.com/{username}/"

    # モバイルUAでアクセス（公開情報を取得しやすい）
    html = _fetch(result["profile_url"], mobile=True)
    if not html:
        result["error"] = "ページ取得失敗"
        return result

    # --- ① OGP/metaタグから基本情報 ---
    soup = BeautifulSoup(html, "lxml")

    # description: "1,234 Followers, 567 Following, 89 Posts - 株式会社XXX..."
    desc_tag = soup.find("meta", {"name": "description"}) or soup.find("meta", property="og:description")
    if desc_tag:
        desc = desc_tag.get("content", "")
        result["bio"] = desc

        f_m = re.search(r"([\d,]+)\s*Followers", desc)
        following_m = re.search(r"([\d,]+)\s*Following", desc)
        post_m = re.search(r"([\d,]+)\s*Posts", desc)

        if f_m:
            result["followers"] = int(f_m.group(1).replace(",", ""))
        if following_m:
            result["following"] = int(following_m.group(1).replace(",", ""))
        if post_m:
            result["post_count"] = int(post_m.group(1).replace(",", ""))

    # --- ② JSON-LD / __ESR__ から追加情報 ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                if data.get("@type") == "ProfilePage" or "interactionStatistic" in data:
                    for stat in data.get("interactionStatistic", []):
                        if "Follower" in stat.get("interactionType", ""):
                            result["followers"] = stat.get("userInteractionCount")
        except Exception:
            pass

    # --- ③ page内の埋め込みJSON (window._sharedData 等) ---
    shared_data_m = re.search(r'window\._sharedData\s*=\s*({.+?});</script>', html)
    if shared_data_m:
        try:
            shared = json.loads(shared_data_m.group(1))
            user = (
                shared.get("entry_data", {})
                .get("ProfilePage", [{}])[0]
                .get("graphql", {})
                .get("user", {})
            )
            if user:
                result["followers"] = user.get("edge_followed_by", {}).get("count")
                result["following"] = user.get("edge_follow", {}).get("count")
                result["post_count"] = user.get("edge_owner_to_timeline_media", {}).get("count")
                result["bio"] = user.get("biography", result["bio"])
                result["is_business"] = user.get("is_business_account", False)

                # 最近の投稿を取得してコンテンツ分析
                edges = user.get("edge_owner_to_timeline_media", {}).get("edges", [])
                if edges:
                    _analyze_ig_posts(edges, result)
        except Exception as e:
            logger.debug(f"sharedData parse error: {e}")

    # --- ④ 診断 ---
    _diagnose_instagram(result)
    return result


def _analyze_ig_posts(edges: list, result: dict):
    """Instagramの投稿データを分析してコンテンツ傾向・エンゲージメントを推定"""
    likes = []
    comments = []
    timestamps = []

    for edge in edges[:12]:
        node = edge.get("node", {})
        likes.append(node.get("edge_liked_by", {}).get("count", 0))
        comments.append(node.get("edge_media_to_comment", {}).get("count", 0))
        ts = node.get("taken_at_timestamp")
        if ts:
            timestamps.append(datetime.fromtimestamp(ts, tz=timezone.utc))

        # キャプションからテーマ推定
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        if caption_edges:
            text = caption_edges[0].get("node", {}).get("text", "")
            _extract_content_themes(text, result)
            # ハッシュタグ収集
            tags = re.findall(r"#([^\s#]+)", text)
            result["hashtags_used"].extend(tags[:5])

    if likes:
        avg_likes = sum(likes) / len(likes)
        result["avg_likes_estimate"] = int(avg_likes)
        if result["followers"] and result["followers"] > 0:
            result["engagement_rate_estimate"] = round(avg_likes / result["followers"] * 100, 2)

    # 投稿頻度を推定
    if len(timestamps) >= 2:
        timestamps.sort(reverse=True)
        recent_days = (timestamps[0] - timestamps[-1]).days
        if recent_days > 0:
            freq_per_week = len(timestamps) / (recent_days / 7)
            if freq_per_week >= 5:
                result["post_frequency_hint"] = "ほぼ毎日"
            elif freq_per_week >= 3:
                result["post_frequency_hint"] = "週3〜5回"
            elif freq_per_week >= 1:
                result["post_frequency_hint"] = "週1〜2回"
            else:
                result["post_frequency_hint"] = "月数回以下"

        # 最終投稿からの経過
        days_since = (datetime.now(tz=timezone.utc) - timestamps[0]).days
        if days_since <= 3:
            result["last_post_hint"] = "3日以内"
        elif days_since <= 7:
            result["last_post_hint"] = "1週間以内"
        elif days_since <= 14:
            result["last_post_hint"] = "2週間以内"
        elif days_since <= 30:
            result["last_post_hint"] = "1ヶ月以内"
        else:
            result["last_post_hint"] = f"約{days_since // 30}ヶ月前"


def _extract_content_themes(text: str, result: dict):
    """テキストからコンテンツテーマを抽出"""
    theme_keywords = {
        "商品紹介": ["新商品", "新発売", "入荷", "商品", "アイテム"],
        "スタッフ・採用": ["スタッフ", "メンバー", "採用", "求人", "一緒に"],
        "セール・キャンペーン": ["セール", "割引", "キャンペーン", "限定", "%OFF", "クーポン"],
        "お知らせ": ["お知らせ", "告知", "リリース", "オープン", "予告"],
        "ブランドストーリー": ["こだわり", "想い", "ストーリー", "理念", "大切に"],
        "UGC・口コミ": ["ご紹介", "ありがとう", "使ってくれた", "レビュー"],
        "生活提案・ライフスタイル": ["暮らし", "日常", "おすすめ", "ライフスタイル"],
    }
    for theme, keywords in theme_keywords.items():
        if any(kw in text for kw in keywords):
            if theme not in result["content_themes"]:
                result["content_themes"].append(theme)


def _diagnose_instagram(result: dict):
    """Instagram診断: 問題点と強みを特定"""
    followers = result["followers"] or 0
    post_count = result["post_count"] or 0
    engagement = result["engagement_rate_estimate"]

    # 問題点
    if post_count < 10:
        result["issues_found"].append("投稿数が非常に少なく（10件未満）、アカウントとして機能していない")
    elif result["post_frequency_hint"] in ["月数回以下", None]:
        result["issues_found"].append("投稿頻度が低く（月数回以下）、アルゴリズムに評価されにくい状態")

    if result["last_post_hint"] and "ヶ月前" in result["last_post_hint"]:
        result["issues_found"].append(f"最終投稿が{result['last_post_hint']}と長期間更新が止まっている")

    if followers > 1000 and engagement and engagement < 1.0:
        result["issues_found"].append(f"フォロワー{followers:,}人に対してエンゲージメント率が{engagement}%と業界平均（1〜3%）を下回っている")

    if not result["is_business"] and followers > 500:
        result["issues_found"].append("ビジネスアカウントに切り替えられておらず、インサイト分析や広告配信ができない状態")

    if not result["hashtags_used"]:
        result["issues_found"].append("ハッシュタグをほぼ使用しておらず、オーガニックリーチが限定的")

    if result["content_themes"] and len(result["content_themes"]) < 2:
        result["issues_found"].append("コンテンツが単一パターンに偏っており、ユーザーの飽きを招きやすい")

    # 強み
    if followers >= 10000:
        result["strengths"].append(f"フォロワー{followers:,}人と一定の認知基盤がある")
    if engagement and engagement >= 3.0:
        result["strengths"].append(f"エンゲージメント率{engagement}%と高くコアファンが育っている")
    if result["post_frequency_hint"] in ["ほぼ毎日", "週3〜5回"]:
        result["strengths"].append("投稿頻度が高く継続的に発信できている")


# ─── TikTok ──────────────────────────────────────────────────

def scrape_tiktok(url_or_username: str) -> dict:
    result = {
        "platform": "tiktok",
        "username": None,
        "profile_url": url_or_username,
        "followers": None,
        "total_likes": None,
        "video_count": None,
        "bio": None,
        "recent_video_titles": [],
        "content_themes": [],
        "issues_found": [],
        "strengths": [],
        "error": None,
    }

    username = _extract_username(url_or_username, "tiktok.com") or url_or_username.lstrip("@")
    if not username:
        result["error"] = "ユーザー名取得失敗"
        return result

    result["username"] = username
    result["profile_url"] = f"https://www.tiktok.com/@{username}"

    html = _fetch(result["profile_url"], mobile=False)
    if not html:
        result["error"] = "ページ取得失敗"
        return result

    # __NEXT_DATA__ または SIGI_STATE からデータ抽出
    for pattern in [
        r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        r'<script id="SIGI_STATE"[^>]*>(.+?)</script>',
        r'"userInfo":\{"user":\{(.+?)\},"stats":\{(.+?)\}',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                # TikTokのJSONは構造が複雑なので再帰的に探す
                _extract_tiktok_data(data, result)
                if result["followers"] is not None:
                    break
            except Exception:
                pass

    # OGPフォールバック
    if result["followers"] is None:
        soup = BeautifulSoup(html, "lxml")
        desc = soup.find("meta", property="og:description")
        if desc:
            text = desc.get("content", "")
            f_m = re.search(r"([\d,\.]+[KMkm]?)\s*(?:Followers|フォロワー)", text)
            if f_m:
                result["followers"] = _parse_tiktok_count(f_m.group(1))

    _diagnose_tiktok(result)
    return result


def _parse_tiktok_count(s: str) -> Optional[int]:
    s = s.replace(",", "").strip()
    try:
        if s.endswith("K") or s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        elif s.endswith("M") or s.endswith("m"):
            return int(float(s[:-1]) * 1000000)
        return int(float(s))
    except Exception:
        return None


def _extract_tiktok_data(data: any, result: dict, depth: int = 0):
    """TikTok JSONから再帰的にデータを探す"""
    if depth > 8 or not isinstance(data, (dict, list)):
        return
    if isinstance(data, list):
        for item in data[:5]:
            _extract_tiktok_data(item, result, depth + 1)
        return

    # followerCount / followingCount / videoCount
    if "followerCount" in data and result["followers"] is None:
        result["followers"] = data["followerCount"]
    if "heartCount" in data and result["total_likes"] is None:
        result["total_likes"] = data["heartCount"]
    if "videoCount" in data and result["video_count"] is None:
        result["video_count"] = data["videoCount"]
    if "signature" in data and result["bio"] is None:
        result["bio"] = data["signature"]

    for v in data.values():
        if isinstance(v, (dict, list)):
            _extract_tiktok_data(v, result, depth + 1)


def _diagnose_tiktok(result: dict):
    followers = result["followers"] or 0
    videos = result["video_count"] or 0

    if videos < 5:
        result["issues_found"].append("動画投稿数が5本未満でアカウントとしてほぼ機能していない")
    elif videos < 20:
        result["issues_found"].append(f"動画投稿数{videos}本と少なく、TikTokのアルゴリズムに乗り切れていない（最低50本が目安）")

    if followers > 0 and result["total_likes"]:
        ratio = result["total_likes"] / max(followers, 1)
        if ratio < 5:
            result["issues_found"].append("総いいね数がフォロワー数比で低く、コンテンツの拡散力が弱い")

    if followers == 0 or followers is None:
        result["issues_found"].append("TikTokアカウントが存在しないか非公開のため、若年層へのリーチが完全に欠落")
    elif followers < 1000:
        result["issues_found"].append(f"フォロワー{followers}人と少なく、TikTokのポテンシャルを活かせていない")

    if followers >= 5000:
        result["strengths"].append(f"TikTokフォロワー{followers:,}人と一定のリーチがある")


# ─── YouTube ─────────────────────────────────────────────────

def scrape_youtube(url_or_handle: str) -> dict:
    result = {
        "platform": "youtube",
        "channel_url": url_or_handle,
        "channel_name": None,
        "subscribers": None,
        "total_views": None,
        "video_count": None,
        "recent_videos": [],          # [{title, date, view_count}]
        "avg_views_per_video": None,
        "upload_frequency_hint": None,
        "content_themes": [],
        "issues_found": [],
        "strengths": [],
        "error": None,
    }

    # チャンネルIDをURLから抽出
    channel_id = _extract_youtube_channel_id(url_or_handle)
    if channel_id:
        result["channel_url"] = f"https://www.youtube.com/channel/{channel_id}"

    html = _fetch(result["channel_url"] + "/videos", mobile=False)
    if not html:
        result["error"] = "ページ取得失敗"
        return result

    # ytInitialData からデータ抽出
    m = re.search(r'var ytInitialData\s*=\s*({.+?});</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            _extract_youtube_data(data, result)
        except Exception as e:
            logger.debug(f"YouTube JSON parse error: {e}")

    # OGPフォールバック
    if not result["channel_name"]:
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("meta", property="og:title")
        if title:
            result["channel_name"] = title.get("content", "")

    # RSS フィードから動画日程を取得（チャンネルIDが判明している場合）
    if channel_id and not result["recent_videos"]:
        _fetch_youtube_rss(channel_id, result)

    _diagnose_youtube(result)
    return result


def _extract_youtube_channel_id(url: str) -> Optional[str]:
    m = re.search(r"youtube\.com/channel/([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)
    return None


def _fetch_youtube_rss(channel_id: str, result: dict):
    """YouTube RSS フィードから最近の動画情報を取得"""
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    html = _fetch(rss_url)
    if not html:
        return

    soup = BeautifulSoup(html, "xml")
    entries = soup.find_all("entry")[:10]

    videos = []
    for entry in entries:
        title = entry.find("title")
        published = entry.find("published")
        views_tag = entry.find("yt:statistics") or entry.find("media:statistics")

        video = {
            "title": title.text if title else "",
            "date": published.text[:10] if published else "",
            "view_count": int(views_tag.get("views", 0)) if views_tag else None,
        }
        videos.append(video)
        if title:
            _extract_content_themes(title.text, result)

    result["recent_videos"] = videos

    # アップロード頻度
    if len(videos) >= 2:
        try:
            d1 = datetime.fromisoformat(entries[0].find("published").text[:10])
            d_last = datetime.fromisoformat(entries[-1].find("published").text[:10])
            days = max((d1 - d_last).days, 1)
            freq = len(videos) / (days / 7)
            if freq >= 3:
                result["upload_frequency_hint"] = "週3本以上"
            elif freq >= 1:
                result["upload_frequency_hint"] = "週1本程度"
            elif freq >= 0.5:
                result["upload_frequency_hint"] = "月2〜3本"
            else:
                result["upload_frequency_hint"] = "月1本以下"
        except Exception:
            pass

    # 平均再生数
    view_counts = [v["view_count"] for v in videos if v["view_count"]]
    if view_counts:
        result["avg_views_per_video"] = int(sum(view_counts) / len(view_counts))


def _extract_youtube_data(data: dict, result: dict, depth: int = 0):
    if depth > 10:
        return
    if isinstance(data, list):
        for item in data[:10]:
            _extract_youtube_data(item, result, depth + 1)
        return
    if not isinstance(data, dict):
        return

    if "subscriberCountText" in data:
        txt = data["subscriberCountText"]
        if isinstance(txt, dict):
            txt = txt.get("simpleText", "")
        result["subscribers"] = txt  # "1.23万人" のような文字列

    if "videosCountText" in data:
        txt = data["videosCountText"]
        if isinstance(txt, dict):
            txt = txt.get("runs", [{}])[0].get("text", "")
        result["video_count"] = txt

    for v in data.values():
        if isinstance(v, (dict, list)):
            _extract_youtube_data(v, result, depth + 1)


def _diagnose_youtube(result: dict):
    if not result["recent_videos"] and not result["subscribers"]:
        result["issues_found"].append("YouTubeチャンネルの情報取得が困難（非公開または運用停止の可能性）")
        return

    if result["upload_frequency_hint"] == "月1本以下":
        result["issues_found"].append("動画アップロードが月1本以下と少なく、チャンネル登録者数が増えにくい状態")
    elif result["upload_frequency_hint"] == "月2〜3本":
        result["issues_found"].append("動画投稿頻度が月2〜3本と低め（週1本以上が推奨）")

    if result["avg_views_per_video"] is not None:
        if result["avg_views_per_video"] < 500:
            result["issues_found"].append(f"平均再生数{result['avg_views_per_video']}回と低く、コンテンツの拡散が課題")

    if result["upload_frequency_hint"] in ["週3本以上", "週1本程度"]:
        result["strengths"].append(f"定期的な動画投稿（{result['upload_frequency_hint']}）が継続できている")

    if result["avg_views_per_video"] and result["avg_views_per_video"] >= 5000:
        result["strengths"].append(f"平均再生数{result['avg_views_per_video']:,}回と高エンゲージメント")


# ─── Twitter / X ──────────────────────────────────────────────

def scrape_twitter(url_or_username: str) -> dict:
    result = {
        "platform": "twitter",
        "username": None,
        "profile_url": url_or_username,
        "followers": None,
        "following": None,
        "tweet_count": None,
        "bio": None,
        "issues_found": [],
        "strengths": [],
        "error": None,
        "note": "Twitter/Xはログインなしでの詳細取得が制限されています",
    }

    username = _extract_username(url_or_username, "twitter.com") or \
               _extract_username(url_or_username, "x.com") or \
               url_or_username.lstrip("@")
    if not username:
        result["error"] = "ユーザー名取得失敗"
        return result

    result["username"] = username
    result["profile_url"] = f"https://x.com/{username}"

    # ログイン不要のキャッシュサービス経由で取得を試みる
    html = _fetch(result["profile_url"], mobile=False)
    if html:
        soup = BeautifulSoup(html, "lxml")
        # OGP
        desc = soup.find("meta", property="og:description")
        if desc:
            result["bio"] = desc.get("content", "")

        # JSON-LDから統計
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if "author" in data or "creator" in data:
                    stats = data.get("interactionStatistic", [])
                    for stat in stats:
                        itype = stat.get("interactionType", "")
                        if "Follower" in itype:
                            result["followers"] = stat.get("userInteractionCount")
            except Exception:
                pass

    # 診断（データが取れなくても最低限の診断は行う）
    _diagnose_twitter(result)
    return result


def _diagnose_twitter(result: dict):
    followers = result["followers"] or 0

    if followers == 0:
        result["issues_found"].append("X(Twitter)の詳細データ取得が制限されているが、アカウントは存在する")
    elif followers < 500:
        result["issues_found"].append(f"Xフォロワー{followers}人と少なく、情報拡散力が低い")

    if followers >= 5000:
        result["strengths"].append(f"Xフォロワー{followers:,}人と一定の影響力がある")


# ─── メイン関数 ───────────────────────────────────────────────

def deep_scrape_all_sns(lead) -> dict:
    """
    Leadオブジェクトの全SNSを実際に訪問して詳細データを収集する

    Returns:
        {
            "instagram": {...} or None,
            "tiktok": {...} or None,
            "youtube": {...} or None,
            "twitter": {...} or None,
            "summary": {
                "platforms_analyzed": int,
                "total_issues": int,
                "overall_assessment": str,
            }
        }
    }
    """
    results = {}

    if lead.instagram_url or lead.has_instagram:
        url = lead.instagram_url or ""
        if url:
            time.sleep(settings.scrape_delay_seconds)
            results["instagram"] = scrape_instagram(url)
            logger.info(f"Instagram scraped: {lead.company_name} → {results['instagram'].get('followers')} followers")

    if lead.tiktok_url or lead.has_tiktok:
        url = lead.tiktok_url or ""
        if url:
            time.sleep(settings.scrape_delay_seconds)
            results["tiktok"] = scrape_tiktok(url)

    if lead.youtube_url or lead.has_youtube:
        url = lead.youtube_url or ""
        if url:
            time.sleep(settings.scrape_delay_seconds)
            results["youtube"] = scrape_youtube(url)

    if lead.twitter_url or lead.has_twitter:
        url = lead.twitter_url or ""
        if url:
            time.sleep(settings.scrape_delay_seconds)
            results["twitter"] = scrape_twitter(url)

    # サマリー生成
    total_issues = sum(
        len(v.get("issues_found", [])) for v in results.values() if v
    )
    platforms = len([v for v in results.values() if v and not v.get("error")])

    if total_issues >= 5:
        assessment = "改善余地が非常に大きく、支援効果が高い見込み"
    elif total_issues >= 2:
        assessment = "複数の改善ポイントがあり提案しやすい状況"
    elif platforms == 0:
        assessment = "SNS未活用またはデータ取得不可"
    else:
        assessment = "一定の運用ができているが最適化の余地あり"

    results["summary"] = {
        "platforms_analyzed": platforms,
        "total_issues": total_issues,
        "overall_assessment": assessment,
    }

    return results
