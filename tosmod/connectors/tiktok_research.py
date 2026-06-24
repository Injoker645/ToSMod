"""TikTok Research API connector (official, academic approval required)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from tosmod.connectors.base import BaseConnector


class TikTokResearchConnector(BaseConnector):
    connector_id = "tiktok_research"
    platform = "tiktok"

    def __init__(self) -> None:
        self.client_key = os.environ.get("TIKTOK_RESEARCH_CLIENT_KEY", "")
        self.client_secret = os.environ.get("TIKTOK_RESEARCH_CLIENT_SECRET", "")
        self.base_url = os.environ.get(
            "TIKTOK_RESEARCH_BASE_URL", "https://open.tiktokapis.com"
        )

    def is_configured(self) -> bool:
        return bool(self.client_key and self.client_secret)

    def collect_comments(self, video_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("TikTok Research API credentials not set. Apply at developers.tiktok.com/products/research-api")
        # Stub: token exchange + comment query — users extend with approved credentials
        raise NotImplementedError(
            "TikTok Research API requires approved researcher credentials. "
            "See docs/DATA_SOURCES.md for application steps."
        )
