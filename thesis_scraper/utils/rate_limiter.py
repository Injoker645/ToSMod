"""
Token-bucket style rate limiting per platform.
Per-platform delays to avoid blocks.
"""
import asyncio
import time
from typing import Dict, Optional


class RateLimiter:
    """Simple rate limiter: wait at least `min_interval` seconds between calls per key."""

    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str = "default") -> None:
        async with self._lock:
            now = time.monotonic()
            last = self._last.get(key, 0)
            wait = max(0, self.min_interval - (now - last))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[key] = time.monotonic()

    def set_interval(self, key: str, interval: float) -> None:
        """Set min interval for a key (e.g. per-platform)."""
        # Use a dict of intervals per key if needed
        if not hasattr(self, "_intervals"):
            self._intervals: Dict[str, float] = {}
        self._intervals[key] = interval

    def get_interval(self, key: str) -> float:
        if getattr(self, "_intervals", None) and key in self._intervals:
            return self._intervals[key]
        return self.min_interval


class PlatformRateLimiter:
    """Rate limiter with per-platform intervals."""

    def __init__(
        self,
        tiktok: float = 2.0,
        instagram: float = 20.0,
        youtube_api: float = 1.0,
        youtube_ytdlp: float = 2.0,
    ):
        self._limiters: Dict[str, RateLimiter] = {
            "tiktok": RateLimiter(tiktok),
            "instagram": RateLimiter(instagram),
            "youtube_api": RateLimiter(youtube_api),
            "youtube_ytdlp": RateLimiter(youtube_ytdlp),
        }

    async def acquire(self, platform: str) -> None:
        limiter = self._limiters.get(platform) or self._limiters["tiktok"]
        await limiter.acquire(platform)
