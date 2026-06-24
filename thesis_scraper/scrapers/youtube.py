"""
YouTube Shorts scraper: Data API v3 (Tier 1), yt-dlp comments (Tier 2).
"""
import asyncio
import json
import logging
import re
import subprocess
from typing import Any, List, Optional

import httpx

from thesis_scraper.scrapers.base import BaseScraper
from thesis_scraper.storage.raw import save_raw_comments_batch

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeScraper(BaseScraper):
    """YouTube Shorts: API v3 first, yt-dlp fallback."""

    def __init__(
        self,
        rate_limiter=None,
        api_key: Optional[str] = None,
        raw_dir: Optional[str] = None,
        comments_source: str = "api",
        **kwargs,
    ):
        super().__init__("youtube_api", rate_limiter=rate_limiter, **kwargs)
        self.api_key = api_key
        self.raw_dir = raw_dir or "data/raw"
        # Comments route: api | ytdlp (separate paths, no fallback)
        self.comments_source = comments_source

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """Extract video ID from YouTube URL (video or shorts)."""
        patterns = [
            r"(?:youtube\.com/watch\?v=|youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})",
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    async def scrape_post_api(self, url: str) -> Optional[dict]:
        """Fetch video metadata via YouTube Data API v3."""
        video_id = self._extract_video_id(url)
        if not video_id or not self.api_key:
            return None
        await self.acquire_rate_limit()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{YOUTUBE_API_BASE}/videos",
                params={
                    "part": "snippet,statistics",
                    "id": video_id,
                    "key": self.api_key,
                },
            )
            if r.status_code != 200:
                return None
            data = r.json()
        items = data.get("items", [])
        if not items:
            return None
        v = items[0]
        sn = v.get("snippet", {})
        st = v.get("statistics", {})
        from datetime import datetime
        published = sn.get("publishedAt")
        ts = None
        if published:
            try:
                ts = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
            except Exception:
                pass
        return {
            "id": video_id,
            "desc": sn.get("description", ""),
            "createTime": ts,
            "author": {"id": sn.get("channelId", ""), "title": sn.get("channelTitle", "")},
            "stats": {
                "playCount": int(st.get("viewCount", 0) or 0),
                "diggCount": int(st.get("likeCount", 0) or 0),
                "shareCount": 0,
                "commentCount": int(st.get("commentCount", 0) or 0),
            },
        }

    async def _fetch_replies(self, parent_comment_id: str) -> List[dict]:
        """Fetch all replies for a top-level comment via comments.list. Preserves thread."""
        replies = []
        page_token = None
        while True:
            await self.acquire_rate_limit()
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{YOUTUBE_API_BASE}/comments",
                    params={
                        "part": "snippet",
                        "parentId": parent_comment_id,
                        "maxResults": 100,
                        "key": self.api_key,
                        **({"pageToken": page_token} if page_token else {}),
                    },
                )
                if r.status_code != 200:
                    break
                data = r.json()
            for item in data.get("items", []):
                sn = item.get("snippet", {})
                replies.append({
                    "id": item.get("id"),
                    "cid": item.get("id"),
                    "text": sn.get("textDisplay", sn.get("textOriginal", "")),
                    "digg_count": sn.get("likeCount", 0),
                    "reply_comment_total": 0,
                    "create_time": None,
                    "published_at": sn.get("publishedAt"),
                    "parent_comment_id": parent_comment_id,
                    "author": sn.get("authorDisplayName"),
                    "channel_id": sn.get("authorChannelId", {}).get("value"),
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        # Normalize create_time from published_at
        for c in replies:
            if c.get("published_at") and not c.get("create_time"):
                try:
                    from datetime import datetime
                    c["create_time"] = int(datetime.fromisoformat(c["published_at"].replace("Z", "+00:00")).timestamp())
                except Exception:
                    pass
        return replies

    async def scrape_comments_api(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> List[dict]:
        """Fetch comment threads (top first via order=relevance) and all replies; preserve thread order."""
        video_id = post_id or self._extract_video_id(post_url)
        if not video_id or not self.api_key:
            return []
        all_comments: List[dict] = []
        page_token = None
        while True:
            await self.acquire_rate_limit()
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{YOUTUBE_API_BASE}/commentThreads",
                    params={
                        "part": "snippet",
                        "videoId": video_id,
                        "maxResults": 100,
                        "order": "relevance",  # top comments first
                        "key": self.api_key,
                        **({"pageToken": page_token} if page_token else {}),
                    },
                )
                if r.status_code != 200:
                    break
                data = r.json()
            for item in data.get("items", []):
                top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                top_cid = item.get("snippet", {}).get("topLevelComment", {}).get("id")
                thread_position = len(all_comments)
                top_dict = {
                    "id": item.get("id"),
                    "cid": top_cid,
                    "text": top.get("textDisplay", top.get("textOriginal", "")),
                    "digg_count": top.get("likeCount", 0),
                    "reply_comment_total": item.get("snippet", {}).get("totalReplyCount", 0),
                    "create_time": None,
                    "published_at": top.get("publishedAt"),
                    "parent_comment_id": None,
                    "author": top.get("authorDisplayName"),
                    "channel_id": top.get("authorChannelId", {}).get("value"),
                    "depth": 0,
                    "thread_position": thread_position,
                    "thread_id": top_cid,
                    "order_in_thread": 0,
                }
                if top_dict.get("published_at") and not top_dict.get("create_time"):
                    try:
                        from datetime import datetime
                        top_dict["create_time"] = int(
                            datetime.fromisoformat(top_dict["published_at"].replace("Z", "+00:00")).timestamp()
                        )
                    except Exception:
                        pass
                all_comments.append(top_dict)
                # Fetch replies to keep full thread for analysis
                reply_count = item.get("snippet", {}).get("totalReplyCount", 0)
                if reply_count and (max_comments is None or len(all_comments) < max_comments):
                    replies = await self._fetch_replies(top_cid)
                    for j, reply in enumerate(replies):
                        if max_comments and len(all_comments) >= max_comments:
                            break
                        reply["depth"] = 1
                        reply["thread_position"] = len(all_comments)
                        reply["thread_id"] = top_cid
                        reply["order_in_thread"] = 1 + j
                        all_comments.append(reply)
            if save_raw and self.raw_dir:
                save_raw_comments_batch(
                    self.raw_dir,
                    "youtube",
                    video_id,
                    {"items": data.get("items", []), "nextPageToken": data.get("nextPageToken")},
                    source="commentThreads",
                )
            page_token = data.get("nextPageToken")
            if not page_token or (max_comments and len(all_comments) >= max_comments):
                break
        if max_comments:
            all_comments = all_comments[:max_comments]
        return all_comments

    async def scrape_comments_ytdlp(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
        save_raw: bool = True,
    ) -> List[dict]:
        """Fetch comments via yt-dlp (Tier 2)."""
        url = post_url if post_url.startswith("http") else f"https://www.youtube.com/shorts/{post_id}"
        await self.acquire_rate_limit()
        try:
            proc = subprocess.run(
                [
                    "yt-dlp",
                    "--no-download",
                    "--write-comments",
                    "--print", "%(comments)j",
                    "--no-warnings",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("yt-dlp failed: %s", e)
            return []
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        try:
            comments_data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        out = []
        for c in comments_data if isinstance(comments_data, list) else getattr(comments_data, "comments", []):
            if isinstance(c, dict):
                out.append({
                    "cid": c.get("id"),
                    "text": c.get("text", ""),
                    "digg_count": c.get("like_count", 0),
                    "reply_comment_total": c.get("reply_count", 0),
                    "create_time": c.get("timestamp"),
                    "parent_comment_id": c.get("parent"),
                    "author": c.get("author"),
                    "channel_id": c.get("author_id"),
                })
        if save_raw and self.raw_dir and post_id:
            save_raw_comments_batch(
                self.raw_dir,
                "youtube",
                post_id,
                {"comments": out},
                source="yt-dlp",
            )
        if max_comments:
            out = out[:max_comments]
        return out

    async def search_videos(
        self,
        query: str,
        max_results: int = 25,
        video_duration: str = "short",
    ) -> List[str]:
        """Search YouTube Data API v3 for video URLs matching *query*.

        Cost: 100 quota units per call (each call returns up to 50 results).
        With the free 10,000 unit/day quota you can do ~100 searches/day.

        Args:
            query: keyword or hashtag string (e.g. "#mentalhealth").
            max_results: total videos to return (capped at 50 per API call;
                         multiple pages fetched automatically up to this limit).
            video_duration: "short" (<= 4 min, covers Shorts) | "medium" | "long" | "any".

        Returns:
            List of YouTube watch URLs, e.g. ["https://www.youtube.com/watch?v=XXXXXXXXXXX", ...]
        """
        if not self.api_key:
            logger.warning("search_videos: no API key configured")
            return []
        urls: List[str] = []
        page_token = None
        while len(urls) < max_results:
            batch = min(50, max_results - len(urls))
            await self.acquire_rate_limit()
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{YOUTUBE_API_BASE}/search",
                    params={
                        "part": "snippet",
                        "q": query,
                        "type": "video",
                        "videoDuration": video_duration,
                        "maxResults": batch,
                        "key": self.api_key,
                        **({"pageToken": page_token} if page_token else {}),
                    },
                )
                if r.status_code != 200:
                    logger.warning("YouTube search failed: %s %s", r.status_code, r.text[:200])
                    break
                data = r.json()
            for item in data.get("items", []):
                vid_id = item.get("id", {}).get("videoId")
                if vid_id:
                    urls.append(f"https://www.youtube.com/watch?v={vid_id}")
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        logger.info("YouTube search '%s': found %s video URLs", query, len(urls))
        return urls[:max_results]

    async def scrape_post(self, url: str) -> Optional[dict]:
        """Scrape post: YouTube API only (no HTML scrape needed for metadata)."""
        return await self.scrape_post_api(url)

    async def scrape_comments(
        self,
        post_id: str,
        post_url: str,
        max_comments: Optional[int] = None,
    ) -> List[dict]:
        """Scrape comments via the configured route only (no fallbacks). Routes: api | ytdlp."""
        source = (self.comments_source or "api").strip().lower()
        save_raw = bool(self.raw_dir)
        if source == "api":
            if not self.api_key:
                logger.warning("YouTube source=api but no API key; returning no comments")
                return []
            return await self.scrape_comments_api(post_id, post_url, max_comments, save_raw=save_raw)
        if source == "ytdlp":
            return await self.scrape_comments_ytdlp(post_id, post_url, max_comments, save_raw=save_raw)
        logger.warning("YouTube unknown comments_source=%s; use api | ytdlp", source)
        return []
