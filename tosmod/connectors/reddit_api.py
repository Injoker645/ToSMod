"""Reddit Data API connector via PRAW (official, ToS-permitted)."""

from __future__ import annotations

import os
from typing import Any

from tosmod.connectors.base import BaseConnector


class RedditAPIConnector(BaseConnector):
    connector_id = "reddit_official"
    platform = "reddit"

    def __init__(self) -> None:
        self.client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self.user_agent = os.environ.get("REDDIT_USER_AGENT", "ToSMod/0.1 research")

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def collect_comments(self, submission_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET not set")
        try:
            import praw
        except ImportError as e:
            raise RuntimeError("Install praw: pip install praw") from e
        reddit = praw.Reddit(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
        )
        submission = reddit.submission(id=submission_id)
        submission.comments.replace_more(limit=0)
        out: list[dict[str, Any]] = []
        limit = int(kwargs.get("max_results", 200))
        for i, comment in enumerate(submission.comments.list()):
            if i >= limit:
                break
            out.append(
                {
                    "comment_id": comment.id,
                    "text": comment.body or "",
                    "author_id": str(comment.author) if comment.author else "deleted",
                    "posted_at": str(comment.created_utc),
                    "platform": "reddit",
                    "post_id": submission_id,
                    "parent_comment_id": comment.parent_id,
                }
            )
        return out
