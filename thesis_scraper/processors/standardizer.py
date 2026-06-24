"""
Convert platform-specific post/comment data to unified schema.
"""
from datetime import datetime, timezone
from typing import Any, List, Optional

from thesis_scraper.processors.anonymizer import anonymize_author
from thesis_scraper.processors.timestamp import normalize_timestamp
from thesis_scraper.storage.models import PostMetrics, UnifiedComment, UnifiedPost
from thesis_scraper.utils.text import clean_html_text


def _scraped_at_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


# --- TikTok ---
def tiktok_post_to_unified(
    raw: dict,
    url: str,
    platform: str = "tiktok",
    salt: str = "",
) -> UnifiedPost:
    """Convert TikTok post dict (from HTML/API) to UnifiedPost."""
    post_id = str(raw.get("id", ""))
    author = raw.get("author") or {}
    raw_author_id = author.get("uniqueId") or author.get("id") or ""
    author_id = anonymize_author(platform, str(raw_author_id), salt) if salt else str(raw_author_id)
    stats = raw.get("stats") or {}
    posted_at, _ = normalize_timestamp(raw.get("createTime"), platform)
    if not posted_at:
        posted_at = _scraped_at_iso()
    scraped_at = _scraped_at_iso()
    metrics = PostMetrics(
        views=int(stats.get("playCount", 0) or 0),
        likes=int(stats.get("diggCount", 0) or 0),
        shares=int(stats.get("shareCount", 0) or 0),
        comments_count=int(stats.get("commentCount", 0) or 0),
    )
    return UnifiedPost(
        platform=platform,
        post_id=post_id,
        url=url,
        author_id=author_id,
        caption=raw.get("desc", ""),
        posted_at=posted_at,
        metrics=metrics,
        scraped_at=scraped_at,
        raw=raw,
    )


def tiktok_comment_to_unified(
    raw: dict,
    platform: str = "tiktok",
    post_id: str = "",
    depth: int = 0,
    thread_position: int = 0,
    thread_id: Optional[str] = None,
    order_in_thread: int = 0,
    salt: str = "",
    scraped_at_iso: Optional[str] = None,
) -> UnifiedComment:
    """Convert TikTok comment dict to UnifiedComment."""
    scraped = scraped_at_iso or _scraped_at_iso()
    cid = str(raw.get("cid") or raw.get("id", ""))
    tid = thread_id or raw.get("thread_id") or (cid if depth == 0 else None)
    ord_in = raw.get("order_in_thread", order_in_thread)
    user = raw.get("user") or {}
    raw_author = user.get("uniqueId") or user.get("unique_id") or user.get("id") or raw.get("username", "")
    author_id = anonymize_author(platform, str(raw_author), salt) if salt else str(raw_author)
    text = raw.get("text", "")
    create_time = raw.get("create_time")
    posted_at, platform_raw_ts = normalize_timestamp(create_time, platform, scraped)
    if not posted_at:
        posted_at = scraped
    # GIF/sticker: populated by Apify when a comment contains an image sticker
    has_gif = bool(raw.get("has_gif") or raw.get("gif_url") or raw.get("imageList"))
    gif_url = raw.get("gif_url") or None
    gif_id  = str(raw["gif_id"]) if raw.get("gif_id") else None

    return UnifiedComment(
        comment_id=cid,
        parent_comment_id=str(raw["parent_comment_id"]) if raw.get("parent_comment_id") else None,
        author_id=author_id,
        text=text,
        posted_at=posted_at,
        likes=int(raw.get("digg_count", 0) or 0),
        reply_count=int(raw.get("reply_comment_total", 0) or 0),
        depth=depth,
        thread_position=thread_position,
        thread_id=tid,
        order_in_thread=ord_in,
        platform_raw_timestamp=platform_raw_ts or (str(create_time) if create_time is not None else None),
        scraped_at=scraped,
        has_gif=has_gif,
        gif_url=gif_url,
        gif_id=gif_id,
        raw=raw,
    )


# --- Instagram ---
def instagram_post_to_unified(
    raw: dict,
    url: str,
    platform: str = "instagram",
    salt: str = "",
) -> UnifiedPost:
    """Convert Instagram post dict to UnifiedPost."""
    post_id = str(raw.get("id", ""))
    author = raw.get("author") or {}
    raw_author_id = author.get("username") or author.get("id") or ""
    author_id = anonymize_author(platform, str(raw_author_id), salt) if salt else str(raw_author_id)
    stats = raw.get("stats") or {}
    posted_at, _ = normalize_timestamp(raw.get("createTime"), platform)
    if not posted_at:
        posted_at = _scraped_at_iso()
    metrics = PostMetrics(
        views=int(stats.get("playCount", 0) or 0),
        likes=int(stats.get("diggCount", 0) or 0),
        shares=int(stats.get("shareCount", 0) or 0),
        comments_count=int(stats.get("commentCount", 0) or 0),
    )
    return UnifiedPost(
        platform=platform,
        post_id=post_id,
        url=url,
        author_id=author_id,
        caption=raw.get("caption", ""),
        posted_at=posted_at,
        metrics=metrics,
        scraped_at=_scraped_at_iso(),
        raw=raw,
    )


def instagram_comment_to_unified(
    raw: dict,
    platform: str = "instagram",
    depth: int = 0,
    thread_position: int = 0,
    thread_id: Optional[str] = None,
    order_in_thread: int = 0,
    salt: str = "",
    scraped_at_iso: Optional[str] = None,
) -> UnifiedComment:
    """Convert Instagram comment dict to UnifiedComment."""
    scraped = scraped_at_iso or _scraped_at_iso()
    cid = str(raw.get("id", ""))
    tid = thread_id or raw.get("thread_id") or (cid if depth == 0 else None)
    ord_in = raw.get("order_in_thread", order_in_thread)
    raw_author = raw.get("username") or raw.get("owner_id", "")
    author_id = anonymize_author(platform, str(raw_author), salt) if salt else str(raw_author)
    posted_at, platform_raw_ts = normalize_timestamp(raw.get("create_time"), platform, scraped)
    if not posted_at:
        posted_at = scraped

    # GIF/image metadata — present when comment came from the browser extension
    has_gif = bool(raw.get("has_gif") or raw.get("gif_url"))
    gif_url = raw.get("gif_url") or None
    gif_id = str(raw["gif_id"]) if raw.get("gif_id") else None

    return UnifiedComment(
        comment_id=cid,
        parent_comment_id=str(raw["parent_comment_id"]) if raw.get("parent_comment_id") else None,
        author_id=author_id,
        text=raw.get("text", ""),
        posted_at=posted_at,
        likes=int(raw.get("digg_count", 0) or 0),
        reply_count=int(raw.get("reply_comment_total", 0) or 0),
        depth=depth,
        thread_position=thread_position,
        thread_id=tid,
        order_in_thread=ord_in,
        platform_raw_timestamp=platform_raw_ts or (str(raw.get("create_time")) if raw.get("create_time") is not None else None),
        scraped_at=scraped,
        has_gif=has_gif,
        gif_url=gif_url,
        gif_id=gif_id,
        raw=raw,
    )


# --- YouTube ---
def youtube_post_to_unified(
    raw: dict,
    url: str,
    platform: str = "youtube",
    salt: str = "",
) -> UnifiedPost:
    """Convert YouTube video dict to UnifiedPost."""
    post_id = str(raw.get("id", ""))
    author = raw.get("author") or {}
    raw_author_id = author.get("id") or author.get("channelId", "")
    author_id = anonymize_author(platform, str(raw_author_id), salt) if salt else str(raw_author_id)
    stats = raw.get("stats") or {}
    posted_at, _ = normalize_timestamp(raw.get("createTime"), platform)
    if not posted_at:
        posted_at = _scraped_at_iso()
    metrics = PostMetrics(
        views=int(stats.get("playCount", 0) or 0),
        likes=int(stats.get("diggCount", 0) or 0),
        shares=int(stats.get("shareCount", 0) or 0),
        comments_count=int(stats.get("commentCount", 0) or 0),
    )
    caption = clean_html_text(raw.get("desc", ""))
    return UnifiedPost(
        platform=platform,
        post_id=post_id,
        url=url,
        author_id=author_id,
        caption=caption,
        posted_at=posted_at,
        metrics=metrics,
        scraped_at=_scraped_at_iso(),
        raw=raw,
    )


def youtube_comment_to_unified(
    raw: dict,
    platform: str = "youtube",
    depth: int = 0,
    thread_position: int = 0,
    thread_id: Optional[str] = None,
    order_in_thread: int = 0,
    salt: str = "",
    scraped_at_iso: Optional[str] = None,
) -> UnifiedComment:
    """Convert YouTube comment dict (from API or yt-dlp) to UnifiedComment."""
    cid = str(raw.get("cid") or raw.get("id", ""))
    tid = thread_id or raw.get("thread_id") or (cid if depth == 0 else None)
    ord_in = raw.get("order_in_thread", order_in_thread)
    raw_author = raw.get("channel_id") or raw.get("author_id") or raw.get("author", "")
    author_id = anonymize_author(platform, str(raw_author), salt) if salt else str(raw_author)
    raw_text = raw.get("text", "") or raw.get("textDisplay", "") or raw.get("textOriginal", "")
    text = clean_html_text(raw_text)
    create_time = raw.get("create_time") or raw.get("timestamp")
    if not create_time and raw.get("published_at"):
        try:
            from datetime import datetime, timezone
            create_time = int(datetime.fromisoformat(raw["published_at"].replace("Z", "+00:00")).timestamp())
        except Exception:
            pass
    scraped = scraped_at_iso or _scraped_at_iso()
    posted_at, platform_raw_ts = normalize_timestamp(create_time, platform, scraped)
    if not posted_at:
        posted_at = scraped
    return UnifiedComment(
        comment_id=cid,
        parent_comment_id=str(raw["parent_comment_id"]) if raw.get("parent_comment_id") else None,
        author_id=author_id,
        text=text,
        posted_at=posted_at,
        likes=int(raw.get("digg_count", 0) or raw.get("like_count", 0) or 0),
        reply_count=int(raw.get("reply_comment_total", 0) or raw.get("reply_count", 0) or 0),
        depth=depth,
        thread_position=thread_position,
        thread_id=tid,
        order_in_thread=ord_in,
        platform_raw_timestamp=platform_raw_ts or (str(create_time) if create_time is not None else None),
        scraped_at=scraped,
        raw=raw,
    )


# --- Dispatcher ---
def standardize_post(raw: dict, url: str, platform: str, salt: str = "") -> UnifiedPost:
    """Dispatch to platform-specific post standardizer."""
    if platform == "tiktok":
        return tiktok_post_to_unified(raw, url, platform, salt)
    if platform == "instagram":
        return instagram_post_to_unified(raw, url, platform, salt)
    if platform == "youtube":
        return youtube_post_to_unified(raw, url, platform, salt)
    raise ValueError(f"Unknown platform: {platform}")


def standardize_comment(
    raw: dict,
    platform: str,
    post_id: str = "",
    depth: int = 0,
    thread_position: int = 0,
    thread_id: Optional[str] = None,
    order_in_thread: int = 0,
    salt: str = "",
    scraped_at_iso: Optional[str] = None,
) -> UnifiedComment:
    """Dispatch to platform-specific comment standardizer."""
    scraped = scraped_at_iso or _scraped_at_iso()
    tid = thread_id or raw.get("thread_id")
    ord_in = raw.get("order_in_thread", order_in_thread)
    if platform == "tiktok":
        return tiktok_comment_to_unified(raw, platform, post_id, depth, thread_position, tid, ord_in, salt, scraped)
    if platform == "instagram":
        return instagram_comment_to_unified(raw, platform, depth, thread_position, tid, ord_in, salt, scraped)
    if platform == "youtube":
        return youtube_comment_to_unified(raw, platform, depth, thread_position, tid, ord_in, salt, scraped)
    raise ValueError(f"Unknown platform: {platform}")
