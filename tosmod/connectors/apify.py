"""Generic Apify actor connector (opt-in tier)."""

from __future__ import annotations

import os
from typing import Any

from tosmod.connectors.base import BaseConnector


class ApifyConnector(BaseConnector):
    connector_id = "apify"
    platform = "custom"

    def __init__(self, actor_id: str, platform: str = "custom") -> None:
        self.actor_id = actor_id
        self.platform = platform
        self.api_key = os.environ.get("APIFY_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def collect_comments(self, target: str, **kwargs: Any) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("APIFY_API_KEY not set")
        try:
            from apify_client import ApifyClient
        except ImportError as e:
            raise RuntimeError("Install apify-client: pip install apify-client") from e
        client = ApifyClient(self.api_key)
        run_input = kwargs.get("run_input") or {"startUrls": [{"url": target}]}
        run = client.actor(self.actor_id).call(run_input=run_input)
        dataset = client.dataset(run["defaultDatasetId"])
        items = list(dataset.iterate_items())
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("comment") or item.get("commentText") or ""
            cid = item.get("id") or item.get("commentId") or item.get("cid")
            out.append(
                {
                    "comment_id": str(cid) if cid else None,
                    "text": str(text),
                    "platform": self.platform,
                    "post_id": item.get("videoId") or item.get("post_id") or target,
                    "raw_json": item,
                }
            )
        return [r for r in out if r.get("text")]
