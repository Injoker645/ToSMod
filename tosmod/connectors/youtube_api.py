"""YouTube Data API v3 connector (official)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from tosmod.connectors.base import BaseConnector

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIConnector(BaseConnector):
    connector_id = "youtube_official"
    platform = "youtube"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def collect_comments(self, video_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("YOUTUBE_API_KEY not set")
        max_results = int(kwargs.get("max_results", 100))
        out: list[dict[str, Any]] = []
        page_token = None
        with httpx.Client(timeout=30.0) as client:
            while len(out) < max_results:
                params: dict[str, Any] = {
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": min(100, max_results - len(out)),
                    "key": self.api_key,
                    "textFormat": "plainText",
                }
                if page_token:
                    params["pageToken"] = page_token
                r = client.get(f"{YOUTUBE_API_BASE}/commentThreads", params=params)
                r.raise_for_status()
                data = r.json()
                for item in data.get("items", []):
                    snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                    out.append(
                        {
                            "comment_id": item.get("snippet", {}).get("topLevelComment", {}).get("id"),
                            "text": snippet.get("textDisplay", ""),
                            "author_id": snippet.get("authorChannelId", {}).get("value", "unknown"),
                            "posted_at": snippet.get("publishedAt"),
                            "platform": "youtube",
                            "post_id": video_id,
                        }
                    )
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return out
