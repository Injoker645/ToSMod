# Thesis Short-Form Video Scraper

Scrapes TikTok, Instagram Reels, and YouTube Shorts (post metadata + comments), standardizes to a unified schema, anonymizes author IDs, and stores in SQLite. For thesis research use.

**Handover / status:** See project root **[HANDOVER.md](../HANDOVER.md)** for original goal, per-platform status (YouTube ✓, TikTok Apify/limited, Instagram comments failing), and next steps (e.g. browser automation for Instagram).

## Setup

```bash
cd c:\Uni\Thesis
python -m pip install -r thesis_scraper/requirements.txt
playwright install chromium   # optional, for TikTok/Instagram browser fallback
```

- **YouTube**: Set `YOUTUBE_API_KEY` or add `youtube_api.api_key` in `thesis_scraper/config/settings.yaml`.
- **TikTok Research API** (optional): Apply at [TikTok for Developers](https://developers.tiktok.com/), then set `tiktok_research_api.access_token` or env.
- **Instagram Reels**: Use a **logged-in session** (Instagram often returns 403 unauthenticated). **Keep username/password in .env only** (never in config):
  1. In `.env` set `INSTAGRAM_USERNAME` and `INSTAGRAM_PASSWORD` (see `.env.example`). Or run the script and type password when prompted.
  2. Create a session once: `python scripts/instagram_login_session.py` (or `python scripts/instagram_login_session.py YOUR_USERNAME`). Session is saved to `data/instagram_session` and username to `data/instagram_username.txt`.
  3. In `thesis_scraper/config/settings.yaml` set `instagram.session_file: "data/instagram_session"`.
  4. Run: `python -m thesis_scraper.main instagram "https://www.instagram.com/reel/SHORTCODE/" --mode post+comments`.
  You can remove `INSTAGRAM_PASSWORD` from `.env` after the session is created. Without a session, the scraper falls back to Playwright (minimal data, no comments).

- **Instagram comments (InstaScrape)** – For comment scraping via authenticated GraphQL, set `instagram.source: instascrape` and create `data/instagram_cookie.json`:
  - **Option A**: `python scripts/instascrape_login.py` (mobile-API login). If you get `block_eu_user_login_in_old_app` or "update Instagram to the latest version", use Option B.
  - **Option B**: `python scripts/instascrape_cookie_from_instaloader.py` – exports cookie from your existing Instaloader session (no extra login; use after `instagram_login_session.py`).
  - In config set `instagram.instascrape_cookie_path: "data/instagram_cookie.json"`.

## Usage

Single URL:

```bash
python -m thesis_scraper.main youtube "https://www.youtube.com/shorts/VIDEO_ID" --mode post+comments --max-comments 100
python -m thesis_scraper.main tiktok "https://www.tiktok.com/@user/video/123" --mode post+comments
python -m thesis_scraper.main instagram "https://www.instagram.com/reel/ABC/" --mode post-only
```

List of URLs (with checkpoint/resume):

```bash
echo https://www.youtube.com/shorts/VID1 > urls.txt
echo https://www.youtube.com/shorts/VID2 >> urls.txt
python -m thesis_scraper.main youtube urls.txt --list urls.txt --mode post+comments --checkpoint data/checkpoint_done.txt --resume
```

## TikTok comments (important for thesis)

TikTok’s website often does **not** expose the comment list API or predictable DOM to automated browsers (region/anti-bot or layout changes). So comment scraping via Playwright may often return **0 comments** even when the video has comments.

**Recommended for thesis:** use the **TikTok Research API** for comments (free for researchers):

1. Apply at [TikTok for Developers – Research API](https://developers.tiktok.com/doc/research-api-get-started).
2. After approval, set an access token in config (or env) and the scraper will use `POST https://open.tiktokapis.com/v2/research/video/comment/list/` for comments (structured, with `parent_comment_id` for threads).
3. Document in your methodology that comment data was collected via the Research API and note any limitations (e.g. quota, geographic bias) per existing literature.

Comments use **separate routes** (no fallbacks): set `tiktok.source` to `research_api`, `playwright`, or `apify`; set `youtube.source` to `api` or `ytdlp`. All routes write the same unified schema (interoperable).

## Output

- **Raw**: `data/raw/<platform>/<date>/` (HTML/JSON per post and comment batches).
- **DB**: `data/thesis_scraper.db` (SQLite) with `posts` and `comments` in unified schema.
- **Config**: `thesis_scraper/config/settings.yaml` (rate limits, API keys, paths).

## Scrape source (dashboard)

Each post row stores **post_source** and **comments_source** so you can see which method was used (e.g. `youtube_api`, `tiktok_apify`, `instagram_instaloader`). The dashboard shows “Post: … · Comments: …” per post.

## Thread order (for analysis)

- **YouTube**: Comment threads are fetched with `order=relevance` (top comments first). Replies are fetched via `comments.list` with `parentId` and appended after each top-level comment so **thread_position** and **depth** preserve the full reply chain.
- **TikTok / Instagram**: Comments are reordered so parents come before replies; **depth** and **thread_position** are set from **parent_comment_id** so threads stay intact for analysis.

## Instagram Reels

- **URLs**: Reels `https://www.instagram.com/reel/SHORTCODE/` or `/reels/SHORTCODE/`, posts `https://www.instagram.com/p/SHORTCODE/`.
- **Session required**: Create a session (see Setup) and set `instagram.session_file` in config. Without it, only Playwright fallback runs (minimal data, no comments).
- **Comments often fail**: Instagram’s internal API frequently returns “We're sorry, but something went wrong” for the comments endpoint. Post metadata (caption, likes, etc.) usually works; comment fetching is unreliable and depends on Instagram’s current restrictions. This is an Instagram limitation, not a bug in the scraper.

## Schema (unified)

- **Post**: `platform`, `post_id`, `url`, `author_id` (hashed), `caption`, `posted_at`, `metrics` (views, likes, shares, comments_count), `scraped_at`, `post_source`, `comments_source`.
- **Comment**: `comment_id`, `parent_comment_id`, `author_id` (hashed), `text`, `posted_at`, `likes`, `reply_count`, `depth`, `thread_position`, **`thread_id`** (root comment_id for the chain), **`order_in_thread`** (0 = root, 1 = first reply, …), `platform_raw_timestamp`, `scraped_at`. **Primary key**: `(platform, post_id, comment_id)`. Group comments by `thread_id` to get full chains for analysis.
