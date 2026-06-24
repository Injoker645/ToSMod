"""
TikTok scraper: Research API, HTML script parser, hidden comment API (XHR),
Playwright DOM fallback. Raw storage for all.
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    from apify_client import ApifyClient
except ImportError:
    ApifyClient = None

from thesis_scraper.scrapers.base import BaseScraper
from thesis_scraper.storage.models import RawCommentPayload, RawPostPayload
from thesis_scraper.storage.raw import save_raw_comments_batch, save_raw_post
from thesis_scraper.utils.stealth import get_common_headers
from thesis_scraper.utils.tiktok_comment_media import extract_apify_tiktok_comment_sticker

logger = logging.getLogger(__name__)

# Script tag IDs for hidden data (TikTok may use different ones)
TIKTOK_SCRIPT_IDS = ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "__DEFAULT_SCOPE__")
TIKTOK_VIDEO_DETAIL_PATH = ["__DEFAULT_SCOPE__", "webapp.video-detail", "itemInfo", "itemStruct"]
# Alternative: some pages nest under __DEFAULT_SCOPE__ only
TIKTOK_VIDEO_DETAIL_PATH_ALT = ["webapp.video-detail", "itemInfo", "itemStruct"]
TIKTOK_COMMENT_LIST_ENDPOINT = "https://www.tiktok.com/api/comment/list/"
TIKTOK_COMMENT_REPLY_PATTERN = "api/comment/list/reply"  # or similar; adjust from network capture


class TikTokScraper(BaseScraper):
    """TikTok scraper with tiered fallbacks."""

    def __init__(
        self,
        rate_limiter=None,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        raw_dir: Optional[str] = None,
        research_api_client_key: Optional[str] = None,
        research_api_client_secret: Optional[str] = None,
        research_api_access_token: Optional[str] = None,
        apify_api_key: Optional[str] = None,
        apify_actor_id: str = "BDec00yAmCm1QbMEI",
        comments_source: str = "playwright",
        **kwargs,
    ):
        super().__init__(
            "tiktok",
            rate_limiter=rate_limiter,
            max_attempts=max_attempts,
            base_delay=base_delay,
            **kwargs,
        )
        self.raw_dir = raw_dir or "data/raw"
        self.research_api_client_key = research_api_client_key
        self.research_api_client_secret = research_api_client_secret
        self.research_api_access_token = research_api_access_token
        self.apify_api_key = apify_api_key
        self.apify_actor_id = apify_actor_id
        # Comments route: one of research_api | apify | playwright (separate paths, no fallback)
        self.comments_source = comments_source

    # --- Tier 1: Research API ---
    async def _get_comments_research_api(
        self,
        video_id: str,
        max_comments: Optional[int] = None,
        cursor: int = 0,
    ) -> List[dict]:
        """Fetch comments via official Research API (Tier 1)."""
        if not self.research_api_access_token:
            return []
        url = "https://open.tiktokapis.com/v2/research/video/comment/list/?fields=id,video_id,text,like_count,reply_count,parent_comment_id,create_time"
        headers = {
            "Authorization": f"Bearer {self.research_api_access_token}",
            "Content-Type": "application/json",
        }
        payload = {"video_id": int(video_id), "max_count": 100, "cursor": cursor}
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        comments = data.get("data", {}).get("comments", [])
        # Normalize to internal shape
        out = []
        for c in comments:
            out.append({
                "cid": str(c.get("id")),
                "text": c.get("text", ""),
                "digg_count": c.get("like_count", 0),
                "reply_comment_total": c.get("reply_count", 0),
                "create_time": c.get("create_time"),
                "parent_comment_id": c.get("parent_comment_id"),
                "aweme_id": video_id,
            })
        return out

    async def scrape_comments_research_api(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> List[dict]:
        """Paginate and collect all comments via Research API."""
        await self.acquire_rate_limit()
        all_comments = []
        cursor = 0
        while True:
            batch = await self._get_comments_research_api(post_id, max_comments, cursor)
            if not batch:
                break
            if save_raw and self.raw_dir:
                save_raw_comments_batch(
                    self.raw_dir,
                    "tiktok",
                    post_id,
                    {"comments": batch, "cursor": cursor},
                    source="research_api",
                )
            all_comments.extend(batch)
            cursor += len(batch)
            if max_comments and len(all_comments) >= max_comments:
                break
            if len(batch) < 100:
                break
            await self.acquire_rate_limit()
        return all_comments[:max_comments] if max_comments else all_comments

    # --- Tier 2: Hidden data in HTML ---
    def _parse_comments_from_dom(self, html: str, post_id: str) -> List[dict]:
        """Extract comments from hydrated HTML when XHR capture fails. Uses data-e2e and class selectors."""
        soup = BeautifulSoup(html, "lxml")
        # TikTok web may use comment-item or render comments inside a list container
        items = soup.select('[data-e2e="comment-item"]')
        if not items:
            # Fallback: any element that might wrap a single comment (e.g. list item with comment text)
            items = soup.select('[data-e2e*="comment"]')
        # If we only got 1–2 elements (e.g. "comment list" container), get descendants that look like comment blocks
        if len(items) <= 2:
            for container in items:
                # Children that have substantial text (likely comment body)
                for block in container.select('[class*="Comment"], [class*="comment"], div[class*="Item"]'):
                    if block.get_text(strip=True) and 5 < len(block.get_text(strip=True)) < 2000:
                        items = container.select('div[class*="Item"], li, [role="listitem"]') or [container]
                        break
            if len(items) <= 2 and items:
                items = items[0].find_all(["div", "li"], recursive=True) if items else []
        comments = []
        for i, el in enumerate(items):
            text = ""
            for selector in ('[data-e2e="comment-level-1"]', '[data-e2e="comment-desc"]', '[data-e2e="comment-text"]'):
                te = el.select_one(selector)
                if te:
                    text = te.get_text(separator=" ", strip=True)
                    break
            if not text:
                desc = el.find(class_=lambda c: c and "desc" in str(c).lower())
                if desc:
                    text = desc.get_text(separator=" ", strip=True)
            if not text:
                for span in el.find_all(["span", "p"]):
                    t = span.get_text(strip=True)
                    if t and 2 < len(t) < 2000:
                        text = t
                        break
            author_el = el.select_one('[data-e2e="comment-username"]') or el.select_one('a[href*="/@"]')
            author = author_el.get_text(strip=True) if author_el else ""
            if author and author.startswith("@"):
                author = author[1:]
            likes = 0
            likes_el = el.select_one('[data-e2e="comment-like-count"]') or el.find(class_=lambda c: c and "like" in str(c).lower())
            if likes_el:
                try:
                    likes = int("".join(c for c in likes_el.get_text() if c.isdigit()) or "0")
                except ValueError:
                    pass
            comments.append({
                "cid": f"dom_{post_id}_{i}",
                "text": (text or "")[:5000],
                "digg_count": likes,
                "reply_comment_total": 0,
                "create_time": None,
                "parent_comment_id": None,
                "aweme_id": post_id,
                "user": {"uniqueId": author, "nickname": author},
            })
        return comments

    def _debug_dom_comments(self, html: str, post_id: str) -> None:
        """Save DOM snippet and log selector counts when DOM extraction finds 0 comments.
        Writes valid HTML so the file opens in a browser: closes tags if truncated,
        adds base URL, and rewrites protocol-relative URLs to https so file:// works.
        """
        soup = BeautifulSoup(html, "lxml")
        for sel, name in [
            ('[data-e2e="comment-item"]', "comment-item"),
            ("[data-e2e*='comment']", "comment-any"),
            ("[class*='Comment']", "class-Comment"),
            ("[class*='comment']", "class-comment"),
        ]:
            n = len(soup.select(sel))
            if n:
                logger.info("TikTok DOM: selector %s matched %s elements", name, n)
        if not self.raw_dir:
            return
        debug_dir = Path(self.raw_dir) / "tiktok_dom_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        max_len = 150000
        snippet = html[:max_len] if len(html) > max_len else html
        if len(html) > max_len:
            last_close = snippet.rfind("</")
            if last_close != -1:
                end_tag = snippet.find(">", last_close)
                if end_tag != -1:
                    snippet = snippet[: end_tag + 1]
            if "</html>" not in snippet:
                if "</body>" not in snippet:
                    snippet += "\n</body></html>"
                else:
                    snippet += "\n</html>"
        if "<head>" in snippet and "<base " not in snippet:
            snippet = snippet.replace("<head>", "<head>\n    <base href=\"https://www.tiktok.com/\">", 1)
        snippet = snippet.replace('href="//', 'href="https://').replace("href='//", "href='https://")
        snippet = snippet.replace('src="//', 'src="https://').replace("src='//", "src='https://")
        (debug_dir / f"page_{post_id}.html").write_text(snippet, encoding="utf-8", errors="replace")

    @staticmethod
    def _hashtag_to_url(tag: str) -> str:
        """Convert a bare hashtag name or #tag to a TikTok hashtag page URL."""
        tag = tag.lstrip("#").strip()
        return f"https://www.tiktok.com/tag/{tag}"

    def _scrape_comments_apify_sync(
        self,
        post_url: str,
        comments_per_post: int,
        post_id: str,
        hashtags: Optional[List[str]] = None,
    ) -> List[dict]:
        """Run Apify TikTok Scraper (sync). Returns list of comments in our internal format.

        Args:
            post_url: single TikTok video URL (used when hashtags is None/empty).
            comments_per_post: max comments to fetch per post.
            post_id: post identifier for raw storage.
            hashtags: optional list of hashtag names/tags (e.g. ["mentalhealth", "#kropp"]).
                If provided, TikTok hashtag page URLs are passed as postURLs instead of post_url,
                causing the actor to discover and scrape videos from each hashtag page.
        """
        if not ApifyClient or not self.apify_api_key:
            return []
        client = ApifyClient(self.apify_api_key)
        # Build the URL list: hashtag pages override a single post URL
        if hashtags:
            post_urls = [self._hashtag_to_url(t) for t in hashtags]
            logger.info("TikTok [apify]: hashtag mode — %s pages: %s", len(post_urls), post_urls)
        else:
            post_urls = [post_url]
        # Minimal input for post URLs + comments; omit optional date filters so actor uses defaults
        run_input = {
            "postURLs": post_urls,
            "commentsPerPost": min(comments_per_post, 100),
            "maxRepliesPerComment": 0,
            "profiles": [],
            "resultsPerPage": 100,
            "profileScrapeSections": ["videos"],
            "profileSorting": "latest",
            "excludePinnedPosts": False,
        }
        logger.info("TikTok [apify]: running actor %s (postURLs=%s)...", self.apify_actor_id, len(post_urls))
        try:
            run = client.actor(self.apify_actor_id).call(run_input=run_input)
        except Exception as exc:
            err_msg = str(exc).lower()
            if any(kw in err_msg for kw in ("limit", "credit", "quota", "payment", "insufficient", "budget", "billing")):
                logger.error(
                    "TikTok [apify]: CREDIT/QUOTA LIMIT reached — actor run not started.\n"
                    "  Progress so far has been saved to the DB.\n"
                    "  → Check your Apify balance at https://console.apify.com/billing\n"
                    "  → Upgrade plan or wait until the monthly credit resets, then resume.\n"
                    "  Raw error: %s",
                    exc,
                )
            else:
                logger.error("TikTok [apify]: actor call failed — %s", exc)
            return []

        run_status = run.get("status", "")
        if run_status != "SUCCEEDED":
            err_msg = (run.get("statusMessage") or "").lower()
            if any(kw in err_msg for kw in ("limit", "credit", "quota", "payment", "insufficient", "budget", "billing")):
                logger.error(
                    "TikTok [apify]: CREDIT/QUOTA LIMIT — actor run %s ended with status %s.\n"
                    "  Progress so far has been saved to the DB.\n"
                    "  → Check your Apify balance at https://console.apify.com/billing\n"
                    "  → Resume remaining URLs after topping up or at the next monthly reset.\n"
                    "  Status message: %s",
                    run.get("id"),
                    run_status,
                    run.get("statusMessage"),
                )
            else:
                logger.error(
                    "TikTok [apify]: actor run %s ended with status %s (message: %s). "
                    "Partial results (if any) will still be returned.",
                    run.get("id"),
                    run_status,
                    run.get("statusMessage"),
                )
            # Fall through: still harvest any partial dataset that was written before failure

        comments = []
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            if not isinstance(item, dict):
                continue
            # Clockworks TikTok Scraper: each item can be a post with nested "comments" array
            nested = item.get("comments") if isinstance(item.get("comments"), list) else None
            if nested:
                for c in nested:
                    if not isinstance(c, dict):
                        continue
                    comments.extend(self._apify_item_to_comments(c, post_id, len(comments)))
                continue
            comments.extend(self._apify_item_to_comments(item, post_id, len(comments)))
        if not comments:
            logger.warning("TikTok [apify]: actor returned 0 items (check actor output schema)")
        return comments

    def _apify_item_to_comments(self, item: dict, post_id: str, offset: int) -> List[dict]:
        """Map one Apify dataset item to our comment shape; returns list of 0 or 1.

        Also extracts GIF/sticker metadata from the Apify 'imageList' field, which
        TikTok populates when a user posts an image/GIF sticker as a comment.
        """
        cid = str(
            item.get("id")
            or item.get("cid")
            or item.get("commentId")
            or item.get("comment_id")
            or offset
        )
        text = (
            item.get("text")
            or item.get("commentText")
            or item.get("content")
            or item.get("comment")
            or ""
        )
        author_meta = item.get("author") or item.get("authorMeta") or item.get("user") or {}
        author_id = (
            author_meta.get("uniqueId")
            if isinstance(author_meta, dict)
            else None
        ) or (author_meta.get("unique_id") if isinstance(author_meta, dict) else None) or item.get("authorUniqueId") or item.get("authorId") or ""
        if isinstance(author_id, dict):
            author_id = author_id.get("uniqueId") or str(author_id)
        digg = (
            item.get("diggCount")
            or item.get("likes")
            or item.get("likeCount")
            or item.get("heartCount")
            or 0
        )
        create_time = item.get("createTime") or item.get("create_time") or item.get("timestamp")
        parent = item.get("parent_comment_id") or item.get("parentCommentId") or item.get("replyToCommentId")

        has_gif, gif_url, gif_id = extract_apify_tiktok_comment_sticker(item)
        if gif_id is not None and gif_id == "":
            gif_id = None
        return [{
            "cid": cid,
            "text": str(text)[:5000],
            "digg_count": int(digg) if digg is not None else 0,
            "reply_comment_total": int(item.get("replyCount") or item.get("reply_count") or 0),
            "create_time": create_time,
            "parent_comment_id": str(parent) if parent is not None else None,
            "aweme_id": post_id,
            "user": {"uniqueId": str(author_id), "nickname": str(author_id)} if author_id else {},
            "has_gif": has_gif,
            "gif_url": gif_url,
            "gif_id": gif_id,
        }]

    async def _scrape_comments_apify(
        self,
        post_url: str,
        max_comments: int,
        post_id: str,
        hashtags: Optional[List[str]] = None,
    ) -> List[dict]:
        """Run Apify in thread so we don't block the event loop."""
        return await asyncio.to_thread(
            self._scrape_comments_apify_sync, post_url, max_comments, post_id, hashtags
        )

    async def scrape_hashtag(
        self,
        hashtags: List[str],
        comments_per_post: int = 100,
    ) -> List[dict]:
        """Convenience method: collect comments from one or more TikTok hashtag pages via Apify.

        Passes hashtag page URLs (https://www.tiktok.com/tag/<tag>) to the Apify actor so it
        discovers and scrapes videos from each page, returning comments in our internal format.

        Usage example:
            comments = await scraper.scrape_hashtag(["mentalhealth", "mentalhälsa"], 100)
        """
        return await self._scrape_comments_apify(
            post_url="",
            max_comments=comments_per_post,
            post_id="hashtag_batch",
            hashtags=hashtags,
        )

    def _extract_item_struct(self, data: dict) -> Optional[dict]:
        """Extract itemStruct from parsed JSON (primary or alt path)."""
        for path in (TIKTOK_VIDEO_DETAIL_PATH, TIKTOK_VIDEO_DETAIL_PATH_ALT):
            node = data
            for key in path:
                if isinstance(node, dict):
                    node = node.get(key)
                else:
                    node = None
                if node is None:
                    break
            if node is not None and isinstance(node, dict):
                return node
        return None

    def _parse_post_from_html(self, html: str) -> Optional[dict]:
        """Parse post metadata from script tag(s). Tries multiple script IDs and paths."""
        soup = BeautifulSoup(html, "lxml")
        data = None
        for script_id in TIKTOK_SCRIPT_IDS:
            script = soup.find("script", id=script_id)
            if script and script.string:
                try:
                    data = json.loads(script.string)
                    break
                except json.JSONDecodeError:
                    continue
        if not data:
            # Fallback: find any script containing video-detail
            for script in soup.find_all("script", id=True):
                if script.string and "webapp.video-detail" in script.string:
                    try:
                        data = json.loads(script.string)
                        break
                    except json.JSONDecodeError:
                        continue
        if not data:
            # Fallback: any script containing itemStruct and video-detail
            for script in soup.find_all("script"):
                if not script.string:
                    continue
                s = script.string.strip()
                if "itemStruct" not in s or "webapp.video-detail" not in s:
                    continue
                try:
                    # May be wrapped in a variable assignment, e.g. window.__DEFAULT_SCOPE__ = {...}
                    start = s.find("{")
                    end = s.rfind("}") + 1
                    if start >= 0 and end > start:
                        data = json.loads(s[start:end])
                        break
                except json.JSONDecodeError:
                    continue
        if not data:
            return None
        # If we got __DEFAULT_SCOPE__ at top level, drill in
        if isinstance(data, dict) and "__DEFAULT_SCOPE__" in data and "webapp.video-detail" not in data:
            data = data["__DEFAULT_SCOPE__"]
        node = self._extract_item_struct(data) if isinstance(data, dict) else None
        if not node:
            return None
        author = node.get("author") or {}
        stats = node.get("stats") or {}
        return {
            "id": str(node.get("id", "")),
            "desc": node.get("desc", ""),
            "createTime": node.get("createTime"),
            "author": {
                "id": str(author.get("id", "")),
                "uniqueId": author.get("uniqueId", ""),
                "nickname": author.get("nickname", ""),
            },
            "stats": {
                "playCount": stats.get("playCount", 0),
                "diggCount": stats.get("diggCount", 0),
                "shareCount": stats.get("shareCount", 0),
                "commentCount": stats.get("commentCount", 0),
            },
        }

    async def scrape_post_html(self, url: str, html: Optional[str] = None) -> Optional[dict]:
        """Scrape post metadata from HTML (Tier 2). If html is None, fetch with httpx."""
        await self.acquire_rate_limit()
        if html is None:
            async with httpx.AsyncClient(http2=True, headers=get_common_headers(), timeout=30) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    return None
                html = r.text
        parsed = self._parse_post_from_html(html)
        return parsed

    async def _scrape_post_playwright_only(self, url: str) -> Optional[dict]:
        """Load video page with Playwright and parse post from hydrated HTML. Use when httpx returns no data."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright not installed; run pip install playwright && playwright install chromium")
            return None
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=get_common_headers().get("User-Agent"),
            )
            page = await context.new_page()
            try:
                from thesis_scraper.utils.stealth import apply_stealth
                await apply_stealth(page, {})
            except Exception:
                pass
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)
            html = await page.content()
            await browser.close()
        return self._parse_post_from_html(html)

    # --- Tier 3/4: Playwright + XHR capture ---
    async def scrape_post_and_comments_playwright(
        self,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> tuple[Optional[dict], List[dict]]:
        """
        Load video page with Playwright, capture post from HTML and comments from XHR.
        Returns (post_dict, list_of_comment_dicts).
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright not installed; run pip install playwright && playwright install")
            return None, []

        xhr_captured: List[Dict[str, Any]] = []
        dom_comments_js: List[dict] = []

        async def handle_route(route):
            request = route.request
            url = request.url
            # Only capture www.tiktok.com API comment list (avoid CDN false positives)
            if "www.tiktok.com" not in url and "tiktok.com" not in url:
                await route.continue_()
                return
            if "api/comment" not in url and "comment/list" not in url:
                await route.continue_()
                return
            try:
                response = await route.fetch()
                body = await response.body()
                body_str = body.decode("utf-8", errors="replace")
                xhr_captured.append({
                    "url": url,
                    "response_body": body_str,
                })
                logger.info("TikTok comment API captured: %s (%s chars)", url[:80], len(body_str))
                await route.fulfill(response=response)
            except Exception as e:
                logger.warning("Route fulfill failed: %s", e)
                await route.continue_()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=get_common_headers().get("User-Agent"),
            )
            await context.route("**/*", handle_route)
            page = await context.new_page()
            try:
                from thesis_scraper.utils.stealth import apply_stealth
                await apply_stealth(page, {})
            except Exception:
                pass
            await page.goto(post_url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(5)
            # Try to open/focus Comments (click comment icon or tab)
            try:
                for sel in ['[data-e2e="comment-icon"]', '[data-e2e="video-comment-icon"]', '[data-e2e="browse-comment-icon"]']:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        await asyncio.sleep(3)
                        break
            except Exception:
                pass
            # Scroll to bottom so comment panel is visible
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            # Wait for comment items to appear (triggers comment API on TikTok web)
            try:
                await page.wait_for_selector('[data-e2e="comment-item"]', timeout=10000)
            except Exception:
                pass
            # Scroll comment list container to load more (TikTok loads on scroll)
            for _ in range(3):
                try:
                    list_el = await page.query_selector('[data-e2e="comment-list"]')
                    if list_el:
                        await list_el.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                    else:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
                except Exception:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
            # Extract comments from live DOM (React-rendered) before closing
            try:
                dom_comments_js = await page.evaluate("""() => {
                    const out = [];
                    const items = document.querySelectorAll('[data-e2e="comment-item"]');
                    items.forEach((el, i) => {
                        const textEl = el.querySelector('[data-e2e="comment-level-1"]') || el.querySelector('[data-e2e="comment-desc"]') || el.querySelector('[class*="desc"]');
                        const authorEl = el.querySelector('[data-e2e="comment-username"]') || el.querySelector('a[href*="/@"]');
                        const likesEl = el.querySelector('[data-e2e="comment-like-count"]') || el.querySelector('[class*="like"]');
                        let text = (textEl && textEl.textContent) ? textEl.textContent.trim() : '';
                        if (!text) { el.querySelectorAll('span, p').forEach(s => { const t = s.textContent.trim(); if (t.length > 2 && t.length < 2000) text = t; }); }
                        let author = (authorEl && authorEl.textContent) ? authorEl.textContent.trim().replace(/^@/, '') : '';
                        let likes = 0; if (likesEl) { const n = (likesEl.textContent || '').replace(/[^0-9]/g, ''); if (n) likes = parseInt(n, 10) || 0; }
                        out.push({ text: text.substring(0, 5000), author, likes, index: i });
                    });
                    return out;
                }""")
            except Exception as e:
                logger.debug("DOM comment extraction failed: %s", e)
            html = await page.content()
            await browser.close()

        if not xhr_captured:
            logger.info("TikTok: no comment API XHR captured; extracting comments from DOM.")
            logger.info("TikTok: For reliable comments, use the Research API (see README). Set tiktok_research_api.access_token in config.")

        post_dict = self._parse_post_from_html(html)
        if post_dict is None:
            post_id = ""
            if "/video/" in post_url:
                m = re.search(r"/video/(\d+)", post_url)
                if m:
                    post_id = m.group(1)
            post_dict = {"id": post_id, "desc": "", "createTime": None, "author": {}, "stats": {}}

        comments = []
        for idx, cap in enumerate(xhr_captured):
            try:
                data = json.loads(cap["response_body"])
            except json.JSONDecodeError:
                continue
            # Debug: save first response to inspect structure
            if idx == 0 and self.raw_dir:
                _debug_path = Path(self.raw_dir) / "tiktok_debug_comment_response.json"
                _debug_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(_debug_path, "w", encoding="utf-8") as f:
                        json.dump({"url": cap["url"], "keys": list(data.keys()) if isinstance(data, dict) else "not_dict", "sample": data if isinstance(data, dict) and len(str(data)) < 2000 else str(data)[:2000]}, f, indent=2)
                except Exception:
                    pass
            # Support multiple response shapes: { comments: [] }, { data: { comments: [] } }, etc.
            raw_list = data.get("comments") or (data.get("data") or {}).get("comments") if isinstance(data.get("data"), dict) else None
            if raw_list is None and isinstance(data, dict):
                for key in ("comments", "commentList", "items"):
                    if key in data and isinstance(data[key], list):
                        raw_list = data[key]
                        break
            if not raw_list:
                continue
            for c in raw_list:
                if not isinstance(c, dict):
                    continue
                comments.append({
                    "cid": str(c.get("cid") or c.get("id") or c.get("comment_id", "")),
                    "text": c.get("text", ""),
                    "digg_count": c.get("digg_count") or c.get("diggCount") or c.get("like_count", 0),
                    "reply_comment_total": c.get("reply_comment_total") or c.get("reply_count", 0),
                    "create_time": c.get("create_time") or c.get("createTime"),
                    "parent_comment_id": c.get("parent_comment_id") or c.get("parentCommentId"),
                    "aweme_id": post_dict.get("id", ""),
                    "user": c.get("user", {}),
                })
            if save_raw and self.raw_dir and post_dict.get("id"):
                save_raw_comments_batch(
                    self.raw_dir,
                    "tiktok",
                    post_dict["id"],
                    data,
                    source="xhr_capture",
                )

        # Fallback: use comments from live DOM (page.evaluate) or parse HTML
        if not comments and dom_comments_js:
            for i, dc in enumerate(dom_comments_js):
                comments.append({
                    "cid": f"dom_{post_dict.get('id', '')}_{i}",
                    "text": dc.get("text", ""),
                    "digg_count": dc.get("likes", 0),
                    "reply_comment_total": 0,
                    "create_time": None,
                    "parent_comment_id": None,
                    "aweme_id": post_dict.get("id", ""),
                    "user": {"uniqueId": dc.get("author", ""), "nickname": dc.get("author", "")},
                })
            if comments:
                logger.info("TikTok: extracted %s comments from live DOM", len(comments))
        if not comments and html:
            comments = self._parse_comments_from_dom(html, post_dict.get("id", ""))
            if comments:
                logger.info("TikTok: extracted %s comments from DOM HTML", len(comments))
            elif html and self.raw_dir:
                # Debug: save snippet and try alternate selectors
                self._debug_dom_comments(html, post_dict.get("id", ""))

        if max_comments:
            comments = comments[:max_comments]
        if save_raw and self.raw_dir and post_dict:
            payload = RawPostPayload(
                platform="tiktok",
                post_id=post_dict.get("id", ""),
                url=post_url,
                raw_html=html,
                raw_json=post_dict,
            )
            save_raw_post(self.raw_dir, payload)
        return post_dict, comments

    # --- BaseScraper interface ---
    async def scrape_post(self, url: str) -> Optional[dict]:
        """Scrape post metadata. Tries httpx first; if no data (TikTok often serves shell without JS), use Playwright."""
        post = await self.scrape_post_html(url)
        if post is not None:
            return post
        logger.info("TikTok: no post data from HTML request, trying Playwright (hydrated page)...")
        return await self._scrape_post_playwright_only(url)

    async def scrape_comments(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
    ) -> List[dict]:
        """
        Scrape comments via the configured route only (no fallbacks).
        Routes: research_api | apify | playwright
        """
        source = (self.comments_source or "playwright").strip().lower()
        save_raw = bool(self.raw_dir)

        if source == "research_api":
            if not self.research_api_access_token:
                logger.warning("TikTok source=research_api but no access token; returning no comments")
                return []
            return await self.scrape_comments_research_api(post_id, post_url, max_comments, save_raw=save_raw)

        if source == "apify":
            if not self.apify_api_key or ApifyClient is None:
                logger.warning("TikTok source=apify but APIFY_API_KEY not set or apify-client not installed; returning no comments")
                return []
            return await self._scrape_comments_apify(post_url, max_comments or 100, post_id)

        if source == "playwright":
            _, comments = await self.scrape_post_and_comments_playwright(post_url, max_comments, save_raw=save_raw)
            return comments

        logger.warning("TikTok unknown comments_source=%s; use research_api | apify | playwright", source)
        return []
