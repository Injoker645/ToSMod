# Opt-in data collectors — user responsibility

**You are solely responsible** for compliance with platform Terms of Service, applicable law, and your institution's ethics requirements when using connectors in this directory.

ToSMod ships these paths as **opt-in only**. They are not enabled by default. Set `TOSMOD_ENABLE_OPT_IN=1` in your environment and acknowledge the legal notice in the Collect tab before use.

## What is included

| Module | Platform | Risk |
|--------|----------|------|
| `tiktok_playwright.py` | TikTok | Violates consumer ToS; may trigger blocks |
| `instagram_instaloader.py` | Instagram | Violates Meta ToS without authorization |
| `instagram_playwright.py` | Instagram | Same |

## Recommended alternatives

- **TikTok:** [Research API](https://developers.tiktok.com/products/research-api) (academic/non-profit application)
- **Instagram:** Meta Content Library for qualified researchers
- **YouTube:** [YouTube Data API v3](https://developers.google.com/youtube/v3) (official)
- **Reddit:** [Reddit Data API](https://www.reddit.com/dev/api/) via PRAW (official)

## Apify

Apify actors are third-party services. You pay Apify credits and remain responsible for whether the actor's access method complies with each platform's ToS.
