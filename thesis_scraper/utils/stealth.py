"""
Playwright stealth configuration: viewport, delays, optional stealth plugin.
Anti-detection utilities for browser automation.
"""
import asyncio
import random
from pathlib import Path
from typing import Optional

# Playwright stealth: try optional plugin
try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    stealth_async = None


def get_viewport(width: int = 1920, height: int = 1080) -> dict:
    """Return a realistic viewport size."""
    return {"width": width, "height": height}


def human_delay(min_sec: float = 2.0, max_sec: float = 5.0) -> float:
    """Return a random delay in seconds for human-like behavior."""
    return random.uniform(min_sec, max_sec)


async def async_human_delay(min_sec: float = 2.0, max_sec: float = 5.0) -> None:
    """Sleep for a random duration (human-like)."""
    await asyncio.sleep(human_delay(min_sec, max_sec))


async def apply_stealth(page, config: Optional[dict] = None) -> None:
    """
    Apply stealth to a Playwright page if playwright-stealth is installed.
    config can include viewport and other options.
    """
    if HAS_STEALTH and stealth_async is not None:
        await stealth_async(page)
    if config:
        viewport = config.get("viewport") or get_viewport(
            config.get("viewport_width", 1920),
            config.get("viewport_height", 1080),
        )
        await page.set_viewport_size(viewport)


def get_common_headers() -> dict:
    """Browser-like headers for requests."""
    return {
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }
