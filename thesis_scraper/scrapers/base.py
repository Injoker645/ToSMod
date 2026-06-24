"""
Abstract base scraper with shared retries and backoff.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, List, Optional, TypeVar

from thesis_scraper.utils.rate_limiter import PlatformRateLimiter

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseScraper(ABC, Generic[T]):
    """Base class for platform scrapers with retry and rate limiting."""

    def __init__(
        self,
        platform: str,
        rate_limiter: Optional[PlatformRateLimiter] = None,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
    ):
        self.platform = platform
        self.rate_limiter = rate_limiter or PlatformRateLimiter()
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base

    async def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        delay = min(
            self.max_delay,
            self.base_delay * (self.exponential_base ** attempt),
        )
        return delay

    async def _with_retry(self, coro, *args, **kwargs) -> Any:
        """Run async callable with retries and exponential backoff."""
        last_exception = None
        for attempt in range(self.max_attempts):
            try:
                return await coro(*args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning("Attempt %s failed: %s", attempt + 1, e)
                if attempt < self.max_attempts - 1:
                    delay = await self._backoff_delay(attempt)
                    await asyncio.sleep(delay)
        raise last_exception

    async def acquire_rate_limit(self) -> None:
        """Apply platform rate limit before a request."""
        await self.rate_limiter.acquire(self.platform)

    @abstractmethod
    async def scrape_post(self, url: str) -> Optional[dict]:
        """Scrape post metadata; return raw dict for standardizer."""

    @abstractmethod
    async def scrape_comments(self, post_id: str, post_url: str, max_comments: Optional[int] = None) -> List[dict]:
        """Scrape comments (and replies) for a post; return list of raw dicts."""
