"""
CLI: platform, mode (post-only / post+comments), URL/list.
Pipeline: standardize -> anonymize -> DB write; logging and checkpointing.
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Add project root for imports (parent of thesis_scraper)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Load .env from project root (YOUTUBE_API_KEY, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

from thesis_scraper.processors.standardizer import standardize_comment, standardize_post
from thesis_scraper.scrapers import InstagramScraper, TikTokScraper, YouTubeScraper
from thesis_scraper.storage import UnifiedComment, UnifiedPost
from thesis_scraper.storage.database import (
    get_connection,
    init_schema,
    insert_comment,
    insert_post,
)
from thesis_scraper.utils.rate_limiter import PlatformRateLimiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("thesis_scraper")


def _scraped_at_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def assign_thread_order(comments_raw: List[dict]) -> List[dict]:
    """Reorder so parents before replies; set depth, thread_position, thread_id, order_in_thread."""
    if not comments_raw:
        return comments_raw
    children: dict = {}
    for c in comments_raw:
        pid = c.get("parent_comment_id")
        pid = str(pid) if pid is not None else None
        children.setdefault(pid, []).append(c)
    ordered = []
    from collections import deque
    # queue: (comment, depth, thread_id, order_in_thread) — order_in_thread is 0 for root, 1,2,3... for replies in thread
    queue = deque((c, 0, str(c.get("cid") or c.get("id") or ""), 0) for c in children.get(None, []))
    while queue:
        c, depth, thread_id, order_in_thread = queue.popleft()
        c["depth"] = depth
        c["thread_position"] = len(ordered)
        c["thread_id"] = thread_id
        c["order_in_thread"] = order_in_thread
        ordered.append(c)
        cid = str(c.get("cid") or c.get("id") or "")
        for i, child in enumerate(children.get(cid, [])):
            queue.append((child, depth + 1, thread_id, order_in_thread + 1 + i))
    # Orphans: parent not in set
    seen = {id(x) for x in ordered}
    for c in comments_raw:
        if id(c) not in seen:
            cid = str(c.get("cid") or c.get("id") or "")
            c["depth"] = c.get("depth", 0)
            c["thread_position"] = len(ordered)
            c["thread_id"] = c.get("thread_id") or cid
            c["order_in_thread"] = c.get("order_in_thread", 0)
            ordered.append(c)
    return ordered


def _scrape_sources(platform: str, scraper) -> tuple[str, str]:
    """Return (post_source, comments_source) for dashboard/labels."""
    if platform == "youtube":
        src = getattr(scraper, "comments_source", "api") or "api"
        return "youtube_api", f"youtube_{src}"
    if platform == "tiktok":
        src = getattr(scraper, "comments_source", "playwright") or "playwright"
        return "tiktok_playwright", f"tiktok_{src}"
    if platform == "instagram":
        src = getattr(scraper, "source", "instaloader") or "instaloader"
        if src == "playwright":
            comments_src = "instagram_playwright"
        elif src == "instascrape":
            comments_src = "instagram_instascrape"
        else:
            comments_src = "instagram_instaloader"
        return "instagram_instaloader", comments_src
    return "unknown", "unknown"


def _load_config_from_project() -> dict:
    """Load config from thesis_scraper/config/settings.yaml."""
    config_path = Path(__file__).resolve().parent / "config" / "settings.yaml"
    if config_path.exists():
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def get_scraper(platform: str, config: dict):
    """Build scraper instance for platform."""
    rate_limits = config.get("rate_limits", {})
    limiter = PlatformRateLimiter(
        tiktok=rate_limits.get("tiktok", 2.0),
        instagram=rate_limits.get("instagram", 20.0),
        youtube_api=rate_limits.get("youtube_api", 1.0),
        youtube_ytdlp=rate_limits.get("youtube_ytdlp", 2.0),
    )
    storage = config.get("storage", {})
    raw_dir = storage.get("raw_dir", "data/raw")
    salt = config.get("anonymization", {}).get("salt", "thesis_salt")

    if platform == "tiktok":
        tiktok_cfg = config.get("tiktok", {}) or {}
        comments_source = (tiktok_cfg.get("source") or "playwright").strip().lower()
        research_cfg = config.get("tiktok_research_api", {})
        apify_cfg = config.get("tiktok_apify", {})
        apify_key = apify_cfg.get("api_key") or __import__("os").environ.get("APIFY_API_KEY")
        logger.info("TikTok comments route: %s", comments_source)
        return TikTokScraper(
            rate_limiter=limiter,
            raw_dir=raw_dir,
            research_api_access_token=research_cfg.get("access_token"),
            research_api_client_key=research_cfg.get("client_key"),
            research_api_client_secret=research_cfg.get("client_secret"),
            apify_api_key=apify_key,
            apify_actor_id=apify_cfg.get("actor_id", "BDec00yAmCm1QbMEI"),
            comments_source=comments_source,
        )
    if platform == "instagram":
        insta = config.get("instagram", {})
        source = (insta.get("source") or "instaloader").strip().lower()
        logger.info("Instagram comments route: %s", source)
        return InstagramScraper(
            rate_limiter=limiter,
            session_file=insta.get("session_file"),
            session_username=insta.get("session_username"),
            cookies_path=insta.get("cookies_path"),
            raw_dir=raw_dir,
            source=source,
            instascrape_cookie_path=insta.get("instascrape_cookie_path"),
        )
    if platform == "youtube":
        yt_cfg = config.get("youtube", {}) or {}
        comments_source = (yt_cfg.get("source") or "api").strip().lower()
        yt = config.get("youtube_api", {})
        api_key = yt.get("api_key") or __import__("os").environ.get("YOUTUBE_API_KEY")
        logger.info("YouTube comments route: %s", comments_source)
        return YouTubeScraper(
            rate_limiter=limiter,
            api_key=api_key,
            raw_dir=raw_dir,
            comments_source=comments_source,
        )
    raise ValueError(f"Unknown platform: {platform}")


async def run_pipeline_async(
    platform: str,
    post_url: str,
    mode: str,
    max_comments: Optional[int] = None,
    config: Optional[dict] = None,
    db_path: Optional[str] = None,
    collection_stratum: Optional[str] = None,
) -> tuple[Optional[UnifiedPost], List[UnifiedComment]]:
    """
    Scrape post (and optionally comments), standardize, anonymize, write to DB.
    mode: "post-only" | "post+comments"
    collection_stratum: one of hashtag | fyp_scroll | sweden | cross_platform (or None)
    """
    cfg = config or _load_config_from_project()
    # Prefer ANONYMIZATION_SALT env var; fall back to settings.yaml value
    import os as _os
    salt = (
        _os.environ.get("ANONYMIZATION_SALT")
        or cfg.get("anonymization", {}).get("salt", "thesis_salt")
    )
    if salt == "thesis_salt_change_in_production":
        logger.warning(
            "ANONYMIZATION_SALT is not set — using placeholder salt. "
            "Set ANONYMIZATION_SALT in .env before the final dataset collection."
        )
    storage = cfg.get("storage", {})
    db_path = db_path or storage.get("db_path", "data/thesis_scraper.db")

    scraper = get_scraper(platform, cfg)
    scraped_at = _scraped_at_iso()

    # Scrape post
    logger.info("Scraping post: %s", post_url)
    post_raw = await scraper.scrape_post(post_url)

    if not post_raw:
        if platform == "tiktok":
            import re as _re
            _m = _re.search(r"/video/(\d+)", post_url)
            if _m:
                logger.error(
                    "TikTok post metadata unavailable for %s.\n"
                    "  → If using Apify mode, the URL must include the creator handle:\n"
                    "    https://www.tiktok.com/@username/video/%s\n"
                    "  → Copy the canonical URL from the TikTok app or web share button,\n"
                    "    not a bare /video/ redirect. Skipping this URL.",
                    post_url,
                    _m.group(1),
                )
            else:
                logger.warning("No post data for %s — skipping.", post_url)
        else:
            logger.warning("No post data for %s — skipping.", post_url)
        return None, []

    post_id = str(post_raw.get("id", ""))
    if not post_id and "/video/" in post_url:
        import re
        m = re.search(r"/video/(\d+)", post_url)
        if m:
            post_id = m.group(1)
    if not post_id and platform == "youtube":
        post_id = YouTubeScraper._extract_video_id(post_url) or ""

    unified_post = standardize_post(post_raw, post_url, platform, salt=salt)
    post_source, comments_source = _scrape_sources(platform, scraper)
    unified_post.post_source = post_source
    unified_post.comments_source = comments_source
    unified_post.collection_stratum = collection_stratum
    conn = get_connection(db_path)
    init_schema(conn)
    insert_post(conn, unified_post)
    conn.close()
    logger.info("Post saved: %s %s", platform, post_id)


    comments: List[UnifiedComment] = []
    if mode == "post+comments":
        logger.info("Scraping comments (max=%s)", max_comments)
        comments_raw = await scraper.scrape_comments(post_id, post_url, max_comments)
        # Preserve thread order: use scraper order if already set, else assign from parent_comment_id
        if comments_raw and not all("thread_position" in c for c in comments_raw):
            comments_raw = assign_thread_order(comments_raw)
        for i, c in enumerate(comments_raw):
            try:
                uc = standardize_comment(
                    c,
                    platform,
                    post_id=post_id,
                    depth=c.get("depth", 0),
                    thread_position=c.get("thread_position", i),
                    thread_id=c.get("thread_id"),
                    order_in_thread=c.get("order_in_thread", 0),
                    salt=salt,
                    scraped_at_iso=scraped_at,
                )
                comments.append(uc)
            except Exception as e:
                logger.warning("Skip comment %s: %s", i, e)
        conn = get_connection(db_path)
        for uc in comments:
            insert_comment(conn, uc, platform, post_id)
        conn.close()
        logger.info("Comments saved: %s", len(comments))

    return unified_post, comments


def run_pipeline(
    platform: str,
    post_url: str,
    mode: str,
    max_comments: Optional[int] = None,
    config: Optional[dict] = None,
    db_path: Optional[str] = None,
    collection_stratum: Optional[str] = None,
) -> tuple[Optional[UnifiedPost], List[UnifiedComment]]:
    """Synchronous wrapper for run_pipeline_async."""
    return asyncio.run(
        run_pipeline_async(platform, post_url, mode, max_comments, config, db_path, collection_stratum)
    )


def main():
    parser = argparse.ArgumentParser(description="Thesis short-form video scraper")
    parser.add_argument("platform", choices=["tiktok", "instagram", "youtube"], help="Platform")
    parser.add_argument("url", nargs="?", default=None, help="Post/reel/short URL (omit when using --search)")
    parser.add_argument(
        "--mode",
        choices=["post-only", "post+comments"],
        default="post+comments",
        help="Scrape post only or post + comments",
    )
    parser.add_argument("--max-comments", type=int, default=None, help="Max comments to fetch")
    parser.add_argument("--config", type=Path, default=None, help="Path to config YAML")
    parser.add_argument("--db", type=str, default=None, help="Database path")
    parser.add_argument("--list", type=Path, default=None, help="Path to file with one URL per line (overrides url). If url is a file path, URLs are read from it.")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Checkpoint file: one URL per line (done). Used with --resume.")
    parser.add_argument("--resume", action="store_true", help="Skip URLs already in checkpoint file (when using --list)")
    parser.add_argument(
        "--stratum",
        default=None,
        help="Collection stratum label stored on the post row (hashtag | fyp_scroll | sweden | cross_platform).",
    )
    # YouTube search mode: discover video URLs via search.list before collecting
    parser.add_argument(
        "--search",
        default=None,
        metavar="QUERY",
        help="(YouTube only) Search query / hashtag. Finds video URLs first, then collects each one. "
             "Each search.list call costs 100 quota units. Combine with --max-results and --stratum.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=25,
        help="Max videos returned by --search (default 25, max 50 per API call).",
    )
    args = parser.parse_args()

    config = _load_config_from_project()
    if args.config and args.config.exists():
        import yaml
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

    # ── Search mode (YouTube only) ──────────────────────────────────────────
    if args.search:
        if args.platform != "youtube":
            logger.error("--search is only supported for the youtube platform")
            sys.exit(1)
        scraper = get_scraper("youtube", config)
        logger.info("YouTube search: '%s' (max_results=%s)", args.search, args.max_results)
        found = asyncio.run(scraper.search_videos(args.search, max_results=args.max_results))
        logger.info("Search returned %s URLs", len(found))
        for url in found:
            try:
                run_pipeline(
                    "youtube",
                    url,
                    args.mode,
                    max_comments=args.max_comments,
                    config=config,
                    db_path=args.db,
                    collection_stratum=args.stratum,
                )
            except Exception as e:
                logger.exception("Failed for %s: %s", url, e)
        return

    # ── Normal URL / list mode ───────────────────────────────────────────────
    if not args.url:
        parser.error("url is required unless --search is used")

    urls: List[str] = []
    list_path = args.list or (Path(args.url) if Path(args.url).is_file() else None)
    if list_path and list_path.exists():
        urls = [u.strip() for u in list_path.read_text(encoding="utf-8").splitlines() if u.strip()]
    else:
        urls = [args.url]

    done_urls: set = set()
    checkpoint_path = args.checkpoint or Path(config.get("storage", {}).get("checkpoint_file", "data/checkpoint_done.txt"))
    if args.resume and checkpoint_path.exists():
        done_urls = {u.strip() for u in checkpoint_path.read_text(encoding="utf-8").splitlines() if u.strip()}
        logger.info("Resume: skipping %s URLs already in checkpoint", len(done_urls))

    for url in urls:
        if args.resume and url in done_urls:
            logger.info("Skip (done): %s", url)
            continue
        try:
            run_pipeline(
                args.platform,
                url,
                args.mode,
                max_comments=args.max_comments,
                config=config,
                db_path=args.db,
                collection_stratum=args.stratum,
            )
            if args.list and checkpoint_path:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                with open(checkpoint_path, "a", encoding="utf-8") as f:
                    f.write(url + "\n")
        except Exception as e:
            logger.exception("Failed for %s: %s", url, e)


if __name__ == "__main__":
    main()
