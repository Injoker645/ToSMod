"""Opt-in scrapers — gated by TOSMOD_ENABLE_OPT_IN=1. See LEGAL.md."""

from __future__ import annotations

import os


def opt_in_enabled() -> bool:
    return os.environ.get("TOSMOD_ENABLE_OPT_IN", "") == "1"


def get_tiktok_playwright():
    if not opt_in_enabled():
        raise RuntimeError("Set TOSMOD_ENABLE_OPT_IN=1 and read LEGAL.md")
    from thesis_scraper.scrapers.tiktok import TikTokScraper
    return TikTokScraper


def get_instagram_instaloader():
    if not opt_in_enabled():
        raise RuntimeError("Set TOSMOD_ENABLE_OPT_IN=1 and read LEGAL.md")
    from thesis_scraper.scrapers.instagram import InstagramScraper
    return InstagramScraper
