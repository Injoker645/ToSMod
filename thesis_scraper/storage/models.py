"""
Unified data models for post and comment schema across platforms.
Pydantic/dataclasses for validation and serialization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class PostMetrics:
    """Unified post engagement metrics."""
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments_count: int = 0


@dataclass
class UnifiedPost:
    """Unified post schema for all platforms."""
    platform: str  # tiktok | instagram | youtube
    post_id: str
    url: str
    author_id: str  # hashed after anonymization
    caption: str
    posted_at: str  # ISO8601
    metrics: PostMetrics
    scraped_at: str  # ISO8601
    collection_stratum: Optional[str] = None  # hashtag | fyp_scroll | sweden | cross_platform
    raw: Optional[dict[str, Any]] = None  # optional platform-specific extras
    post_source: Optional[str] = None  # e.g. youtube_api, tiktok_playwright, instagram_instaloader
    comments_source: Optional[str] = None  # e.g. youtube_api, tiktok_apify, instagram_instaloader


@dataclass
class UnifiedComment:
    """Unified comment schema for all platforms."""
    comment_id: str
    parent_comment_id: Optional[str]  # null = top-level
    author_id: str  # hashed
    text: str
    posted_at: str  # ISO8601
    likes: int
    reply_count: int
    depth: int  # 0 = top-level
    thread_position: int  # global order (root then replies) for sorting
    thread_id: Optional[str] = None  # root comment_id for this chain; same as comment_id for roots
    order_in_thread: int = 0  # 0 = root, 1 = first reply, 2 = second reply, ...
    platform_raw_timestamp: Optional[str] = None  # original for accuracy notes
    scraped_at: Optional[str] = None  # ISO8601 when comment was scraped
    # Multimodal fields — GIF/image comments
    has_gif: bool = False
    gif_url: Optional[str] = None
    gif_id: Optional[str] = None
    # Optional path relative to project root (e.g. archived TikTok CDN sticker)
    gif_local_path: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


@dataclass
class RawPostPayload:
    """Container for raw scraped post data before standardization."""
    platform: str
    post_id: str
    url: str
    raw_html: Optional[str] = None
    raw_json: Optional[dict[str, Any]] = None
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


@dataclass
class RawCommentPayload:
    """Container for raw scraped comment data (e.g. API response batch)."""
    platform: str
    post_id: str
    raw_json: dict[str, Any]
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    source: Optional[str] = None  # e.g. "comment/list", "comment/reply_list"


def post_to_dict(p: UnifiedPost) -> dict[str, Any]:
    """Serialize UnifiedPost to dict for DB/storage."""
    return {
        "platform": p.platform,
        "post_id": p.post_id,
        "url": p.url,
        "author_id": p.author_id,
        "caption": p.caption,
        "posted_at": p.posted_at,
        "metrics": {
            "views": p.metrics.views,
            "likes": p.metrics.likes,
            "shares": p.metrics.shares,
            "comments_count": p.metrics.comments_count,
        },
        "scraped_at": p.scraped_at,
        "collection_stratum": p.collection_stratum,
        "raw": p.raw,
        "post_source": p.post_source,
        "comments_source": p.comments_source,
    }


def comment_to_dict(c: UnifiedComment) -> dict[str, Any]:
    """Serialize UnifiedComment to dict for DB/storage."""
    return {
        "comment_id": c.comment_id,
        "parent_comment_id": c.parent_comment_id,
        "author_id": c.author_id,
        "text": c.text,
        "posted_at": c.posted_at,
        "likes": c.likes,
        "reply_count": c.reply_count,
        "depth": c.depth,
        "thread_position": c.thread_position,
        "thread_id": c.thread_id,
        "order_in_thread": c.order_in_thread,
        "platform_raw_timestamp": c.platform_raw_timestamp,
        "scraped_at": c.scraped_at,
        "has_gif": c.has_gif,
        "gif_url": c.gif_url,
        "gif_id": c.gif_id,
        "gif_local_path": c.gif_local_path,
        "raw": c.raw,
    }
