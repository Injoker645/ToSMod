"""
Instagram Reels scraper: Instaloader (Tier 1), InstaScrape/Playwright (comments).

Reels URLs: https://www.instagram.com/reel/SHORTCODE/
Posts: https://www.instagram.com/p/SHORTCODE/
Both use the same shortcode extraction and Instaloader/Playwright flows.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from thesis_scraper.scrapers.base import BaseScraper
from thesis_scraper.storage.raw import save_raw_comments_batch

logger = logging.getLogger(__name__)

# Project root (parent of thesis_scraper) for resolving relative session_file
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Instagram domain for Playwright cookies
INSTAGRAM_DOMAIN = ".instagram.com"


def _instascrape_cookie_to_playwright(cookie_path: str) -> List[Dict[str, Any]]:
    """Convert InstaScrape-format cookie.json to list of Playwright cookie dicts."""
    p = Path(cookie_path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    cookies = data.get("cookies") or {}
    if not all(cookies.get(k) for k in ("sessionid", "csrftoken", "mid", "ds_user_id")):
        return []
    out = []
    for name, value in cookies.items():
        if value:
            out.append({
                "name": name,
                "value": str(value),
                "domain": INSTAGRAM_DOMAIN,
                "path": "/",
            })
    return out


def _parse_graphql_comment_edges(data: Dict[str, Any]) -> List[dict]:
    """
    Extract comment dicts from Instagram GraphQL response (shortcode_media comment edges).
    Handles edge_media_to_parent_comment and edge_threaded_comments (replies).
    Returns list in our raw format: id, username, owner_id, text, create_time, parent_comment_id, digg_count, reply_comment_total.
    """
    out: List[dict] = []
    try:
        media = data.get("data", {}).get("shortcode_media")
        if not media:
            return out
        parent_edge = media.get("edge_media_to_parent_comment", {})
        edges = parent_edge.get("edges", [])
    except Exception:
        return out
    for edge in edges:
        node = edge.get("node", {})
        owner = node.get("owner", {})
        cid = node.get("id")
        if not cid:
            continue
        out.append({
            "id": cid,
            "username": owner.get("username", ""),
            "owner_id": str(owner.get("id", "")),
            "text": node.get("text", ""),
            "create_time": node.get("created_at"),
            "parent_comment_id": None,
            "digg_count": int((node.get("edge_liked_by") or {}).get("count", 0) or 0),
            "reply_comment_total": int((node.get("edge_threaded_comments") or {}).get("count", 0) or 0),
        })
        # Replies: edge_threaded_comments.edges
        reply_edges = (node.get("edge_threaded_comments") or {}).get("edges", [])
        for reply_edge in reply_edges:
            rnode = reply_edge.get("node", {})
            rowner = rnode.get("owner", {})
            rid = rnode.get("id")
            if rid:
                out.append({
                    "id": rid,
                    "username": rowner.get("username", ""),
                    "owner_id": str(rowner.get("id", "")),
                    "text": rnode.get("text", ""),
                    "create_time": rnode.get("created_at"),
                    "parent_comment_id": cid,
                    "digg_count": int((rnode.get("edge_liked_by") or {}).get("count", 0) or 0),
                    "reply_comment_total": 0,
                })
    return out


class InstagramScraper(BaseScraper):
    """Instagram Reels scraper: Instaloader (post + optional comments) or InstaScrape (comments via GraphQL)."""

    def __init__(
        self,
        rate_limiter=None,
        session_file: Optional[str] = None,
        session_username: Optional[str] = None,
        cookies_path: Optional[str] = None,
        raw_dir: Optional[str] = None,
        source: Optional[str] = None,
        instascrape_cookie_path: Optional[str] = None,
        **kwargs,
    ):
        super().__init__("instagram", rate_limiter=rate_limiter, **kwargs)
        self.session_file = session_file
        self.session_username = session_username
        self.cookies_path = cookies_path
        self.raw_dir = raw_dir or "data/raw"
        self.source = (source or "instaloader").strip().lower()
        self.instascrape_cookie_path = instascrape_cookie_path

    def _instaloader_post_and_comments(self, shortcode: str) -> tuple[Optional[dict], List[dict]]:
        """Use Instaloader (sync) to get post and comments. Returns (post_dict, comments)."""
        try:
            import instaloader
        except ImportError:
            logger.warning("Instaloader not installed; pip install instaloader")
            return None, []

        loader = instaloader.Instaloader()
        # Resolve session_file relative to project root so "data/instagram_session" always works
        session_path = None
        if self.session_file:
            p = Path(self.session_file)
            session_path = p if p.is_absolute() else (_PROJECT_ROOT / p)
        if session_path and session_path.exists():
            try:
                username = self.session_username
                if not username and (session_path.parent / "instagram_username.txt").exists():
                    username = (session_path.parent / "instagram_username.txt").read_text(encoding="utf-8").strip()
                if username:
                    loader.load_session_from_file(username, str(session_path))
                else:
                    loader.load_session_from_file(Path(self.session_file).stem, str(session_path))
            except Exception as e:
                logger.warning(
                    "Could not load Instaloader session from %s: %s. Re-run scripts/instagram_login_session.py.",
                    session_path,
                    e,
                )
        elif self.session_file:
            logger.warning(
                "Instagram session file not found: %s. Run: python scripts/instagram_login_session.py",
                session_path or self.session_file,
            )

        try:
            post = instaloader.Post.from_shortcode(loader.context, shortcode)
        except Exception as e:
            logger.warning("Instaloader post fetch failed: %s", e)
            return None, []

        post_dict = {
            "id": post.shortcode,
            "caption": post.caption or "",
            "createTime": int(post.date_utc.timestamp()) if post.date_utc else None,
            "author": {"id": str(post.owner_id), "username": post.owner_username},
            "stats": {
                "playCount": post.video_view_count or 0,
                "diggCount": post.likes,
                "shareCount": 0,
                "commentCount": post.comments,
            },
        }
        comments = []
        try:
            for i, comment in enumerate(post.get_comments()):
                comments.append({
                    "id": comment.id,
                    "text": comment.text,
                    "digg_count": 0,
                    "reply_comment_total": 0,
                    "create_time": int(comment.created_at_utc.timestamp()) if comment.created_at_utc else None,
                    "parent_comment_id": None,
                    "username": comment.owner.username,
                    "owner_id": str(comment.owner_id),
                })
                if comment.answers:
                    for j, answer in enumerate(comment.answers):
                        comments.append({
                            "id": answer.id,
                            "text": answer.text,
                            "digg_count": 0,
                            "reply_comment_total": 0,
                            "create_time": int(answer.created_at_utc.timestamp()) if answer.created_at_utc else None,
                            "parent_comment_id": comment.id,
                            "username": answer.owner.username,
                            "owner_id": str(answer.owner_id),
                        })
        except Exception as e:
            logger.warning("Instaloader comments fetch failed: %s", e)
            if "something went wrong" in str(e).lower() or "fail" in str(e).lower():
                logger.info(
                    "Instagram often blocks/limits the comments API. Post metadata was saved; comments are 0. See README."
                )
        return post_dict, comments

    async def scrape_post_instaloader(self, url: str) -> Optional[dict]:
        """Extract shortcode from URL and fetch post via Instaloader."""
        shortcode = self._url_to_shortcode(url)
        if not shortcode:
            return None
        await self.acquire_rate_limit()
        post_dict, _ = self._instaloader_post_and_comments(shortcode)
        return post_dict

    def _url_to_shortcode(self, url: str) -> Optional[str]:
        """Extract Instagram shortcode from reel/post URL (e.g. /reel/ABC/, /reels/ABC/, /p/ABC/)."""
        import re
        m = re.search(r"(?:reels?|p)/([A-Za-z0-9_-]+)", url, re.IGNORECASE)
        return m.group(1) if m else None

    async def scrape_comments_instaloader(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> List[dict]:
        """Scrape comments via Instaloader."""
        shortcode = post_id if len(post_id) > 10 else self._url_to_shortcode(post_url)
        if not shortcode:
            return []
        await self.acquire_rate_limit()
        post_dict, comments = self._instaloader_post_and_comments(shortcode)
        if save_raw and self.raw_dir and comments:
            save_raw_comments_batch(
                self.raw_dir,
                "instagram",
                post_id or shortcode,
                {"comments": comments},
                source="instaloader",
            )
        if max_comments:
            comments = comments[:max_comments]
        return comments

    async def scrape_post_playwright(self, url: str) -> Optional[dict]:
        """Load reel with Playwright and parse from DOM/embedded data (Tier 2)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None
        from thesis_scraper.utils.stealth import get_common_headers

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=get_common_headers().get("User-Agent"),
            )
            if self.cookies_path and Path(self.cookies_path).exists():
                context.add_cookies(json.loads(Path(self.cookies_path).read_text(encoding="utf-8")))
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            html = await page.content()
            await browser.close()

        # Minimal extraction: try meta or embedded JSON
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        post_id = self._url_to_shortcode(url) or "unknown"
        caption = ""
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            caption = og_desc["content"]
        return {
            "id": post_id,
            "caption": caption,
            "createTime": None,
            "author": {},
            "stats": {"playCount": 0, "diggCount": 0, "shareCount": 0, "commentCount": 0},
        }

    async def scrape_post(self, url: str) -> Optional[dict]:
        """Scrape post: try Instaloader first, then Playwright."""
        post = await self.scrape_post_instaloader(url)
        if post is None:
            post = await self.scrape_post_playwright(url)
        return post

    async def scrape_comments_playwright(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> List[dict]:
        """
        Scrape comments via Playwright: load cookie or login, open reel, intercept GraphQL
        comment responses; fallback to DOM extraction. Returns list of raw comment dicts.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright not installed; run pip install playwright && playwright install chromium")
            return []

        shortcode = post_id if post_id and len(post_id) <= 20 else self._url_to_shortcode(post_url)
        if not shortcode:
            return []

        await self.acquire_rate_limit()

        # Config: headless, timeout, human delay (from instagram.playwright or top-level playwright)
        try:
            from thesis_scraper.config import get_config
            cfg = get_config()
        except Exception:
            cfg = {}
        pw_global = cfg.get("playwright", {})
        pw_insta = (cfg.get("instagram") or {}).get("playwright", {})
        headless = pw_insta.get("headless", pw_global.get("headless", True))
        timeout_ms = pw_insta.get("timeout_ms", pw_global.get("timeout_ms", 30000))
        delay_min = pw_insta.get("human_delay_min", pw_global.get("human_delay_min", 2.0))
        delay_max = pw_insta.get("human_delay_max", pw_global.get("human_delay_max", 5.0))

        from thesis_scraper.utils.stealth import get_common_headers, async_human_delay

        graphql_captured: List[Dict[str, Any]] = []
        dom_comments_js: List[dict] = []

        async def handle_route(route):
            request = route.request
            url = request.url
            if "instagram.com" not in url or "graphql" not in url or "query" not in url:
                await route.continue_()
                return
            try:
                response = await route.fetch()
                body = await response.body()
                body_str = body.decode("utf-8", errors="replace")
                graphql_captured.append({"url": url, "response_body": body_str})
                await route.fulfill(response=response)
            except Exception as e:
                logger.debug("Instagram route fulfill failed: %s", e)
                await route.continue_()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=get_common_headers().get("User-Agent"),
            )
            # Load cookie if available (InstaScrape-format)
            cookie_path = self.instascrape_cookie_path
            pw_cookies: List[Dict[str, Any]] = []
            if cookie_path:
                pw_cookies = _instascrape_cookie_to_playwright(cookie_path)
                if pw_cookies:
                    await context.add_cookies(pw_cookies)
                    logger.debug("Instagram Playwright: loaded %s cookies", len(pw_cookies))
                else:
                    logger.debug("Instagram Playwright: cookie file missing or invalid, will try login")
            else:
                logger.debug("Instagram Playwright: no cookie path, will try login")

            await context.route("**/*", handle_route)
            page = await context.new_page()
            try:
                from thesis_scraper.utils.stealth import apply_stealth
                await apply_stealth(page, {})
            except Exception:
                pass

            try:
                reel_url = post_url if post_url.startswith("http") else f"https://www.instagram.com/reel/{shortcode}/"
                if not pw_cookies:
                    # No cookies: go to login first, then reel
                    await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=timeout_ms)
                    await async_human_delay(delay_min, delay_max)
                    if "accounts/login" in page.url:
                        username = os.environ.get("INSTAGRAM_USERNAME", "").strip()
                        password = os.environ.get("INSTAGRAM_PASSWORD", "").strip()
                        if username and password:
                            try:
                                # Instagram may use name="username" or aria-label; wait for form
                                username_sel = 'input[name="username"], input[aria-label*="Phone"], input[aria-label*="Username"]'
                                await page.wait_for_selector(username_sel, timeout=15000)
                                await page.fill(username_sel, username)
                                await page.fill('input[name="password"], input[type="password"]', password)
                                await page.click('button[type="submit"]')
                                await async_human_delay(5.0, 7.0)
                            except Exception as e:
                                logger.warning("Instagram Playwright login fill failed: %s", e)
                        else:
                            logger.warning("Instagram Playwright: no cookie and no INSTAGRAM_USERNAME/PASSWORD in env; comments may be empty")
                    else:
                        await async_human_delay(1.0, 2.0)

                # Navigate to reel
                await page.goto(reel_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await async_human_delay(3.0, 5.0)

                # Scroll to load comments and trigger GraphQL
                for _ in range(5):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await async_human_delay(1.0, 3.0)
                # Try to focus/expand comments section (Instagram may use a scrollable div)
                try:
                    comment_sel = "ul[role='list']"
                    await page.wait_for_selector(comment_sel, timeout=8000)
                except Exception:
                    pass
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await async_human_delay(2.0, 4.0)

                # DOM fallback: extract visible comments (selectors may need updating if Instagram changes DOM)
                try:
                    dom_comments_js = await page.evaluate("""() => {
                        const out = [];
                        const uls = document.querySelectorAll('ul[role="list"]');
                        uls.forEach(ul => {
                            ul.querySelectorAll('li').forEach((li, i) => {
                                const spans = li.querySelectorAll('span');
                                const links = li.querySelectorAll('a[href*="/"]');
                                let text = '';
                                let username = '';
                                spans.forEach(s => {
                                    const t = (s.textContent || '').trim();
                                    if (t.length > 1 && t.length < 3000 && !t.match(/^\\d+[hdw]\\s*ago$/i)) text = t;
                                });
                                if (links.length) username = (links[0].textContent || '').trim().replace(/^@/, '');
                                if (text || username) out.push({ text: text.substring(0, 5000), username, index: out.length });
                            });
                        });
                        return out;
                    }""")
                except Exception as e:
                    logger.debug("Instagram DOM comment extraction failed: %s", e)

            except Exception as e:
                logger.warning("Instagram Playwright navigation failed: %s", e)
            finally:
                await browser.close()

        comments: List[dict] = []
        seen_ids: set = set()

        for cap in graphql_captured:
            try:
                data = json.loads(cap["response_body"])
            except json.JSONDecodeError:
                continue
            parsed = _parse_graphql_comment_edges(data)
            for c in parsed:
                cid = c.get("id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    comments.append(c)

        if not comments and dom_comments_js:
            for i, dc in enumerate(dom_comments_js):
                comments.append({
                    "id": f"dom_{shortcode}_{i}",
                    "username": dc.get("username", ""),
                    "owner_id": "",
                    "text": dc.get("text", ""),
                    "create_time": None,
                    "parent_comment_id": None,
                    "digg_count": 0,
                    "reply_comment_total": 0,
                })

        if max_comments:
            comments = comments[:max_comments]

        if save_raw and self.raw_dir and comments:
            save_raw_comments_batch(
                self.raw_dir,
                "instagram",
                post_id or shortcode,
                {"comments": comments},
                source="playwright",
            )

        logger.info("Instagram Playwright: %s comments for shortcode %s", len(comments), shortcode)
        return comments

    async def scrape_comments(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
    ) -> List[dict]:
        """Scrape comments: Playwright when source=playwright, InstaScrape when source=instascrape, else Instaloader."""
        if self.source == "playwright":
            return await self.scrape_comments_playwright(post_id, post_url, max_comments, save_raw=bool(self.raw_dir))
        if self.source == "instascrape" and self.instascrape_cookie_path:
            return await self._scrape_comments_instascrape(post_id, post_url, max_comments)
        return await self.scrape_comments_instaloader(post_id, post_url, max_comments, save_raw=bool(self.raw_dir))

    async def _scrape_comments_instascrape(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
    ) -> List[dict]:
        """Fetch parent comments via authenticated GraphQL (InstaScrape method)."""
        from thesis_scraper.scrapers.instagram_instascrape import fetch_parent_comments

        shortcode = post_id if post_id and len(post_id) <= 20 else self._url_to_shortcode(post_url)
        if not shortcode:
            return []
        await self.acquire_rate_limit()
        rps = 5.0
        comments = await fetch_parent_comments(
            shortcode,
            self.instascrape_cookie_path,
            max_comments=max_comments,
            rps=rps,
        )
        if self.raw_dir and comments:
            save_raw_comments_batch(
                self.raw_dir,
                "instagram",
                post_id or shortcode,
                {"comments": comments},
                source="instascrape",
            )
        return comments
