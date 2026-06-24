"""
Research Collection Dashboard.
Run from project root: python -m dashboard.app
Then open http://127.0.0.1:5050
"""
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
import traceback
import zipfile
import html as html_lib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from flask import Flask, Response, jsonify, render_template, request

# ── Ensure project root is on sys.path so thesis_scraper is always importable,
#    regardless of the working directory the server was started from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from tosmod.config.loader import get_config  # noqa: E402

_TOSMOD_CFG = get_config()

# Hard imports — fail loudly at startup rather than silently at request time.
from thesis_scraper.storage.database import init_schema as db_init_schema  # noqa: E402
from thesis_scraper.main import run_pipeline  # noqa: E402
from thesis_scraper.utils.tiktok_comment_media import extract_apify_tiktok_comment_sticker  # noqa: E402
from dashboard.tosmod_routes import bp as tosmod_bp  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
app.register_blueprint(tosmod_bp)

PROJECT_ROOT = _PROJECT_ROOT
DEFAULT_DB = _TOSMOD_CFG.db_path()
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
CANONICAL_STRATA = ("search_term", "cross_platform")
LEGACY_STRATA_TO_CANONICAL = {
    "fyp_scroll": "search_term",
    "sweden": "search_term",
    "personal_scroll": "search_term",
    "self_selected": "search_term",
}
_strata_migrated = False

# Manual gold target per platform for the Annotate tab (display only).
ANNOTATE_MANUAL_GOAL_COMMENTS_PER_PLATFORM = 1500


def _image_proxy_host_allowed(hostname: str | None) -> bool:
    """Allow only known media CDNs (avoid open proxy)."""
    if not hostname:
        return False
    h = hostname.lower()
    suffixes = (
        "tiktokcdn.com",
        "tiktokcdn-us.com",
        "tiktokcdn-eu.com",
        "tiktokcdn-eu2.com",
        "ttwstatic.com",
        "byteoversea.com",
        "ibyteimg.com",
        "cdninstagram.com",
        "fbcdn.net",
        "instagram.com",
        "ytimg.com",
        "ggpht.com",
        "googleusercontent.com",
        "giphy.com",
        "twimg.com",
    )
    return any(h == s or h.endswith("." + s) for s in suffixes)


@app.route("/api/image-proxy")
def api_image_proxy():
    """Fetch hotlinked CDN images with a browser-like Referer (fixes TikTok CDN 403 in annotate UI)."""
    raw = (request.args.get("url") or "").strip()
    if not raw:
        return "Missing url", 400
    try:
        parsed = urlparse(raw)
    except Exception:
        return "Bad url", 400
    if parsed.scheme not in ("http", "https"):
        return "Invalid scheme", 400
    host = parsed.hostname or ""
    if not _image_proxy_host_allowed(host):
        return "Host not allowed", 403
    hl = host.lower()
    if "instagram" in hl or "fbcdn" in hl:
        referer = "https://www.instagram.com/"
    elif "ytimg" in hl or "youtube" in hl or "googleusercontent" in hl:
        referer = "https://www.youtube.com/"
    else:
        referer = "https://www.tiktok.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ThesisDashboard/1.0; +local research)",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Referer": referer,
    }
    try:
        r = httpx.get(raw, headers=headers, follow_redirects=True, timeout=35.0)
    except Exception as exc:
        app.logger.warning("image-proxy fetch failed for %s: %s", raw[:120], exc)
        return Response("Upstream unreachable", status=502, mimetype="text/plain")

    if r.status_code >= 400:
        return Response(r.content or b"", status=r.status_code, mimetype="text/plain")

    ct = (r.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    return Response(r.content, mimetype=ct)


@app.route("/api/instagram-thumbnail/<shortcode>")
def api_instagram_thumbnail(shortcode: str):
    """Serve a fresh Instagram post thumbnail (stored CDN URLs often expire)."""
    shortcode = (shortcode or "").strip()
    if not shortcode or not re.fullmatch(r"[A-Za-z0-9_-]+", shortcode):
        return "Bad shortcode", 400
    media_url = f"https://www.instagram.com/p/{shortcode}/media/?size=l"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ThesisDashboard/1.0; +local research)",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Referer": "https://www.instagram.com/",
    }
    try:
        r = httpx.get(media_url, headers=headers, follow_redirects=True, timeout=25.0)
    except Exception as exc:
        app.logger.warning("instagram-thumbnail fetch failed for %s: %s", shortcode, exc)
        return Response("Upstream unreachable", status=502, mimetype="text/plain")
    if r.status_code >= 400 or not r.content:
        ig_meta = _fetch_instagram_page_meta(f"https://www.instagram.com/p/{shortcode}/")
        og = (ig_meta.get("thumbnail_url") or "").strip()
        if og:
            try:
                r = httpx.get(og, headers=headers, follow_redirects=True, timeout=25.0)
            except Exception as exc:
                app.logger.warning("instagram-thumbnail og fallback failed for %s: %s", shortcode, exc)
                return Response("Thumbnail unavailable", status=502, mimetype="text/plain")
        else:
            return Response("Thumbnail unavailable", status=404, mimetype="text/plain")
    ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    if not ct.startswith("image/"):
        ct = "image/jpeg"
    return Response(r.content, mimetype=ct)


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_api_key():
    return os.environ.get("YOUTUBE_API_KEY", "")


def get_db():
    db_path = os.environ.get("TOSMOD_DB_PATH") or os.environ.get("THESIS_DB_PATH") or str(DEFAULT_DB)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if db_init_schema:
        try:
            db_init_schema(conn)
        except Exception:
            pass
    # Ensure dashboard-specific columns exist (idempotent)
    _ensure_post_columns(conn)
    _apply_strata_migration(conn)
    return conn


def _ensure_post_columns(conn):
    cur = conn.execute("PRAGMA table_info(posts)")
    existing = {row[1] for row in cur.fetchall()}
    for col, typ in (
        ("collection_stratum", "TEXT"),
        ("search_query", "TEXT"),
        ("post_source", "TEXT"),
        ("comments_source", "TEXT"),
    ):
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {typ}")
                conn.commit()
            except sqlite3.OperationalError:
                conn.rollback()


def _apply_strata_migration(conn):
    """
    Rename legacy strata values to the canonical taxonomy.
    Also normalize TikTok+Instagram rows into the requested cross-platform bucket.
    """
    global _strata_migrated
    if _strata_migrated:
        return
    try:
        for old_value, new_value in LEGACY_STRATA_TO_CANONICAL.items():
            conn.execute(
                "UPDATE posts SET collection_stratum=? WHERE COALESCE(collection_stratum,'')=?",
                (new_value, old_value),
            )
        # Neve / cross-platform comparability: same stratum + search_query as TikTok & Instagram.
        # (Do not blanket-update all TikTok/Instagram rows — only Neve-tagged ones.)
        conn.execute(
            """
            UPDATE posts
            SET collection_stratum = 'cross_platform',
                search_query = 'wisewordsfromneve'
            WHERE LOWER(TRIM(COALESCE(collection_stratum, ''))) IN (
                    'wisewordsfromneve', 'wisewordsfromnev'
                )
                OR (
                    LOWER(TRIM(COALESCE(search_query, ''))) IN (
                        'wisewordsfromneve',
                        'wisewordsfromnev',
                        '@therealwisewordsfromneve',
                        'therealwisewordsfromneve'
                    )
                    AND COALESCE(collection_stratum, '') != 'cross_platform'
                )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
    _strata_migrated = True


def _post_exists(platform: str, post_id: str | None = None, url: str | None = None) -> bool:
    conn = get_db()
    if not conn:
        return False
    try:
        if post_id:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE platform=? AND post_id=? LIMIT 1",
                (platform, post_id),
            ).fetchone()
            if row:
                return True
        if url:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE platform=? AND url=? LIMIT 1",
                (platform, url),
            ).fetchone()
            return bool(row)
        return False
    finally:
        conn.close()


def row_to_dict(row):
    return dict(row) if row is not None else None


def _log_search_run(
    platform: str,
    query_text: str,
    filters: dict,
    result_ids: list[str],
) -> None:
    conn = get_db()
    if not conn:
        return
    try:
        conn.execute(
            """
            INSERT INTO search_runs (platform, query_text, filters_json, result_ids_json, result_count, run_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                query_text,
                json.dumps(filters, ensure_ascii=False),
                json.dumps(result_ids, ensure_ascii=False),
                len(result_ids),
                _iso_now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _log_ingestion_failure(
    platform: str,
    post_id: str | None,
    url: str | None,
    reason_code: str,
    reason_detail: str,
    source_context: str,
) -> None:
    conn = get_db()
    if not conn:
        return
    try:
        conn.execute(
            """
            INSERT INTO ingestion_failures
            (platform, post_id, url, reason_code, reason_detail, source_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (platform, post_id, url, reason_code, reason_detail, source_context, _iso_now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _has_recent_failure(platform: str, post_id: str | None, url: str | None) -> sqlite3.Row | None:
    conn = get_db()
    if not conn:
        return None
    try:
        if post_id:
            row = conn.execute(
                """
                SELECT * FROM ingestion_failures
                WHERE platform=? AND post_id=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (platform, post_id),
            ).fetchone()
            if row:
                return row
        if url:
            row = conn.execute(
                """
                SELECT * FROM ingestion_failures
                WHERE platform=? AND url=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (platform, url),
            ).fetchone()
            return row
        return None
    finally:
        conn.close()


def _delete_post_bundle(platform: str, post_id: str) -> None:
    conn = get_db()
    if not conn:
        return
    try:
        conn.execute("DELETE FROM comments       WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM annotations    WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM video_metadata WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM transcripts    WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM posts          WHERE platform=? AND post_id=?", (platform, post_id))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Stats / posts / comments (existing API preserved) ─────────────────────────

@app.route("/api/stats")
def api_stats():
    conn = get_db()
    if conn is None:
        return jsonify({"error": "Database not found"})
    try:
        cur = conn.cursor()
        cur.execute("SELECT platform, COUNT(*) AS count FROM posts GROUP BY platform")
        by_platform = [{"platform": r["platform"], "count": r["count"]} for r in cur.fetchall()]
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        return jsonify({"total_posts": total_posts, "total_comments": total_comments, "by_platform": by_platform})
    finally:
        conn.close()


@app.route("/api/posts")
def api_posts():
    conn = get_db()
    if conn is None:
        return jsonify({"error": "Database not found", "posts": []})
    platform = request.args.get("platform")
    stratum = (request.args.get("stratum") or "").strip()
    sub_tag = (request.args.get("sub_tag") or "").strip()
    try:
        clauses = []
        params = []
        if platform:
            clauses.append("platform=?")
            params.append(platform)
        if stratum:
            clauses.append("COALESCE(collection_stratum,'')=?")
            params.append(stratum)
        if sub_tag:
            clauses.append("COALESCE(search_query,'')=?")
            params.append(sub_tag)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM posts {where_sql} ORDER BY scraped_at DESC",
            tuple(params),
        ).fetchall()
        return jsonify({"posts": [row_to_dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/posts/<platform>/<post_id>")
def api_post_detail(platform, post_id):
    conn = get_db()
    if conn is None:
        return jsonify({"error": "Database not found"})
    try:
        row = conn.execute(
            "SELECT * FROM posts WHERE platform=? AND post_id=?", (platform, post_id)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        post = row_to_dict(row)
        comments = conn.execute(
            "SELECT * FROM comments WHERE platform=? AND post_id=? ORDER BY thread_position ASC",
            (platform, post_id),
        ).fetchall()
        post["comments"] = [row_to_dict(r) for r in comments]
        return jsonify(post)
    finally:
        conn.close()


@app.route("/api/comments")
def api_comments():
    conn = get_db()
    if conn is None:
        return jsonify({"error": "Database not found", "comments": []})
    platform = request.args.get("platform")
    post_id = request.args.get("post_id")
    try:
        if platform and post_id:
            rows = conn.execute(
                "SELECT * FROM comments WHERE platform=? AND post_id=? ORDER BY thread_position ASC",
                (platform, post_id),
            ).fetchall()
        elif platform:
            rows = conn.execute(
                "SELECT * FROM comments WHERE platform=? ORDER BY post_id, thread_position ASC",
                (platform,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM comments ORDER BY platform, post_id, thread_position ASC"
            ).fetchall()
        return jsonify({"comments": [row_to_dict(r) for r in rows]})
    finally:
        conn.close()


# ── YouTube search ─────────────────────────────────────────────────────────────


def _youtube_handle_for_api(handle_or_url: str) -> str:
    """Normalize to bare handle for channels.list forHandle (no @)."""
    raw = (handle_or_url or "").strip()
    if not raw:
        return ""
    if "youtube.com" in raw or "youtu.be" in raw:
        m = re.search(r"youtube\.com/@([^/?#]+)", raw, re.I)
        if m:
            return m.group(1)
        m = re.search(r"@[\w.-]+", raw)
        if m:
            return m.group(0).lstrip("@")
        return ""
    return raw.lstrip("@")


def _youtube_iso8601_duration_seconds(iso: str) -> int | None:
    """Parse contentDetails.duration (e.g. PT1M5S) to seconds."""
    if not iso or not iso.startswith("PT"):
        return None
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso)
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h or 0) * 3600 + int(mn or 0) * 60 + int(s or 0)


def _resolve_youtube_channel_id(api_key: str, handle: str, explicit_id: str) -> tuple[str | None, int]:
    """Resolve @handle or return explicit UC… id. Returns (channel_id, extra_quota)."""
    quota = 0
    cid = (explicit_id or "").strip()
    if cid:
        if not cid.startswith("UC") or len(cid) < 10:
            return None, 0
        return cid, 0
    h = _youtube_handle_for_api(handle)
    if not h:
        return None, 0
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                f"{YOUTUBE_API_BASE}/channels",
                params={"part": "id", "forHandle": h, "key": api_key},
            )
        quota = 1
        if r.status_code != 200:
            return None, quota
        items = r.json().get("items") or []
        if not items:
            return None, quota
        return items[0].get("id"), quota
    except Exception:
        return None, quota


@app.route("/api/search/youtube")
def api_search_youtube():
    q = request.args.get("q", "").strip()
    channel_handle = request.args.get("channel_handle", "").strip()
    channel_id_param = request.args.get("channel_id", "").strip()
    shorts_strict = str(request.args.get("shorts_strict", "")).lower() in ("1", "true", "yes")

    # target_count: how many qualifying results to return
    target_count = max(1, min(int(request.args.get("target_count", 10)), 50))
    min_views    = max(0, int(request.args.get("min_views", 0)))
    duration     = request.args.get("duration", "short")
    order        = request.args.get("order", "relevance")

    api_key = get_api_key()
    if not api_key:
        return jsonify({"error": "YOUTUBE_API_KEY not configured in .env"}), 500

    resolved_channel_id = None
    resolve_quota = 0
    if channel_handle or channel_id_param:
        resolved_channel_id, resolve_quota = _resolve_youtube_channel_id(
            api_key, channel_handle, channel_id_param
        )
        if not resolved_channel_id:
            detail = "Could not resolve channel_handle / channel_id (check handle spelling and API key)."
            return jsonify({"error": detail}), 400

    if not q and not resolved_channel_id:
        return jsonify({"error": "Provide q (search text) and/or channel_handle / channel_id"}), 400

    MAX_PAGES = 5   # hard cap: 5 × 100 = 500 quota units for search
    results   = []
    page_token = None
    quota_used = resolve_quota
    pages_fetched = 0

    video_parts = "statistics,contentDetails" if shorts_strict else "statistics"

    while len(results) < target_count and pages_fetched < MAX_PAGES:
        # ── search.list (100 quota units per call) ──────────────────────────
        params: dict = {
            "part": "snippet",
            "type": "video",
            "videoDuration": duration,
            "order": order,
            "maxResults": 50,
            "key": api_key,
        }
        if resolved_channel_id:
            params["channelId"] = resolved_channel_id
        if q:
            params["q"] = q
        if page_token:
            params["pageToken"] = page_token

        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(f"{YOUTUBE_API_BASE}/search", params=params)
            if r.status_code != 200:
                if not results:
                    return jsonify({"error": f"YouTube API error ({r.status_code}): {r.text[:300]}"}), 500
                break
            search_data = r.json()
        except Exception as exc:
            if not results:
                return jsonify({"error": f"YouTube request failed: {exc}"}), 500
            break

        quota_used += 100
        pages_fetched += 1
        items = search_data.get("items", [])
        page_token = search_data.get("nextPageToken")

        if not items:
            break

        # ── videos.list for statistics (+ duration for strict Shorts) ────────
        vid_ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
        stats_by_id: dict = {}
        duration_by_id: dict = {}
        if vid_ids:
            try:
                with httpx.Client(timeout=20) as client:
                    r2 = client.get(
                        f"{YOUTUBE_API_BASE}/videos",
                        params={
                            "part": video_parts,
                            "id": ",".join(vid_ids),
                            "key": api_key,
                        },
                    )
                if r2.status_code == 200:
                    for it in r2.json().get("items", []):
                        vid = it["id"]
                        stats_by_id[vid] = it.get("statistics", {})
                        cd = it.get("contentDetails") or {}
                        d_iso = cd.get("duration")
                        if d_iso:
                            duration_by_id[vid] = _youtube_iso8601_duration_seconds(d_iso)
                quota_used += 1
            except Exception:
                pass

        # ── filter and accumulate ────────────────────────────────────────────
        for it in items:
            if len(results) >= target_count:
                break
            vid_id = it.get("id", {}).get("videoId")
            if not vid_id:
                continue
            snippet = it.get("snippet", {})
            stats = stats_by_id.get(vid_id, {})
            views = int(stats.get("viewCount") or 0)

            if min_views > 0 and views < min_views:
                continue

            if shorts_strict:
                secs = duration_by_id.get(vid_id)
                if secs is None or secs < 1 or secs > 180:
                    continue

            comments_disabled = "commentCount" not in stats
            # Prefer /shorts/ URL when strict Shorts filter is on (same video id).
            watch_url = f"https://www.youtube.com/watch?v={vid_id}"
            short_url = f"https://www.youtube.com/shorts/{vid_id}"
            results.append(
                {
                    "id": vid_id,
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "published_at": (snippet.get("publishedAt") or "")[:10],
                    "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg",
                    "views": views,
                    "likes": int(stats.get("likeCount") or 0),
                    "comments_count": int(stats.get("commentCount") or 0) if not comments_disabled else None,
                    "comments_disabled": comments_disabled,
                    "url": short_url if shorts_strict else watch_url,
                    "in_db": False,
                }
            )

        if not page_token:
            break

    # ── mark which are already in DB ────────────────────────────────────────
    conn = get_db()
    if conn:
        try:
            in_db = {r["post_id"] for r in conn.execute(
                "SELECT post_id FROM posts WHERE platform='youtube'"
            ).fetchall()}
            for res in results:
                res["in_db"] = res["id"] in in_db
        finally:
            conn.close()

    log_q = q or f"(channel:{resolved_channel_id})"
    _log_search_run(
        "youtube",
        log_q,
        {
            "target_count": target_count,
            "min_views": min_views,
            "duration": duration,
            "order": order,
            "channel_handle": channel_handle or None,
            "channel_id": channel_id_param or resolved_channel_id,
            "shorts_strict": shorts_strict,
            "quota_used": quota_used,
            "pages_fetched": pages_fetched,
        },
        [r["id"] for r in results],
    )

    return jsonify(
        {
            "results": results,
            "total": len(results),
            "quota_used": quota_used,
            "pages_fetched": pages_fetched,
            "resolved_channel_id": resolved_channel_id,
        }
    )


@app.route("/api/search/history")
def api_search_history():
    platform = (request.args.get("platform") or "").strip()
    limit = max(1, min(int(request.args.get("limit", 100)), 500))
    conn = get_db()
    if conn is None:
        return jsonify({"error": "Database not found", "history": []})
    try:
        if platform:
            rows = conn.execute(
                """
                SELECT id, platform, query_text, filters_json, result_ids_json, result_count, run_at
                FROM search_runs
                WHERE platform=?
                ORDER BY run_at DESC
                LIMIT ?
                """,
                (platform, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, platform, query_text, filters_json, result_ids_json, result_count, run_at
                FROM search_runs
                ORDER BY run_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        history = []
        for r in rows:
            item = row_to_dict(r)
            for key in ("filters_json", "result_ids_json"):
                try:
                    item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
                except Exception:
                    item[key.replace("_json", "")] = {}
            history.append(item)
        return jsonify({"history": history})
    finally:
        conn.close()


# ── Collect a single YouTube video ────────────────────────────────────────────

@app.route("/api/collect/youtube/<video_id>", methods=["POST"])
def api_collect_youtube_one(video_id):
    import threading
    data = request.json or {}
    search_query = data.get("search_query", "")
    stratum      = data.get("stratum") or None
    mode         = data.get("mode", "post+comments")
    max_comments = int(data.get("max_comments", 200))
    enrich       = data.get("enrich", True)   # auto-enrich by default

    url = f"https://www.youtube.com/watch?v={video_id}"
    if _post_exists("youtube", post_id=video_id, url=url):
        return jsonify({
            "status": "skipped",
            "reason_code": "already_exists",
            "reason": "Post already exists in database.",
            "message": "Skipped: this video is already collected.",
            "video_id": video_id,
        }), 200
    prior_failure = _has_recent_failure("youtube", video_id, url)
    if prior_failure:
        pf = row_to_dict(prior_failure)
        return jsonify({
            "status": "skipped",
            "reason_code": pf.get("reason_code"),
            "reason": pf.get("reason_detail"),
            "message": "Skipped: this video has a prior non-collectable failure log.",
        }), 409
    try:
        unified_post, comments = run_pipeline(
            "youtube", url, mode,
            max_comments=max_comments,
            collection_stratum=stratum,
        )
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc), "detail": traceback.format_exc()}), 500

    if unified_post is None:
        _log_ingestion_failure(
            "youtube",
            video_id,
            url,
            "post_unavailable",
            "No post data returned (private/deleted/region-locked).",
            json.dumps({"mode": mode, "max_comments": max_comments}),
        )
        return jsonify({"status": "error", "error": "No post data returned — video may be private, deleted, or region-locked."}), 404

    if mode != "post-only" and len(comments) == 0:
        _log_ingestion_failure(
            "youtube",
            video_id,
            url,
            "zero_comments",
            "Collected zero comments or comments are restricted/disabled.",
            json.dumps({"mode": mode, "max_comments": max_comments, "search_query": search_query}),
        )
        _delete_post_bundle("youtube", video_id)
        return jsonify({
            "status": "dropped",
            "video_id": video_id,
            "reason_code": "zero_comments",
            "reason": "Collected zero comments or comments are restricted/disabled.",
        }), 200

    # Persist search_query (not on the UnifiedPost model — direct SQL UPDATE)
    if search_query:
        conn = get_db()
        if conn:
            try:
                conn.execute(
                    "UPDATE posts SET search_query=? WHERE platform='youtube' AND post_id=?",
                    (search_query, video_id),
                )
                conn.commit()
            finally:
                conn.close()

    # Kick off metadata + transcript enrichment in background (non-blocking)
    if enrich:
        t = threading.Thread(target=enrich_video, args=(video_id, "youtube"), daemon=True)
        t.start()

    return jsonify({
        "status":             "ok",
        "video_id":           video_id,
        "comments_collected": len(comments),
        "mode":               mode,
        "enriching":          enrich,
    })


# ── Enrich: rich metadata + transcript ────────────────────────────────────────

def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _parse_duration(iso: str) -> int:
    """Convert ISO 8601 duration (PT1H2M3S) to total seconds."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mn, s = (int(m.group(i) or 0) for i in (1, 2, 3))
    return h * 3600 + mn * 60 + s


def _extractive_summary(text: str, max_words: int = 150) -> str:
    """Lightweight extractive summary: score sentences by word frequency,
    return top sentences in original order up to max_words."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return ""
    # word frequency (lower-cased, strip punctuation)
    words = re.findall(r"[a-z']+", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if len(w) > 3:
            freq[w] = freq.get(w, 0) + 1
    # score each sentence
    scores = []
    for i, sent in enumerate(sentences):
        ws = re.findall(r"[a-z']+", sent.lower())
        score = sum(freq.get(w, 0) for w in ws) / max(len(ws), 1)
        scores.append((score, i, sent))
    scores.sort(reverse=True)
    # pick top sentences until max_words
    picked = []
    total = 0
    for _, idx, sent in scores:
        wc = len(sent.split())
        if total + wc > max_words and picked:
            break
        picked.append((idx, sent))
        total += wc
    # restore original order
    picked.sort(key=lambda x: x[0])
    return " ".join(s for _, s in picked)


def _fetch_youtube_metadata(video_id: str, api_key: str) -> dict:
    """Fetch snippet + contentDetails for one video. Returns raw dict."""
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{YOUTUBE_API_BASE}/videos", params={
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": api_key,
        })
    if r.status_code != 200:
        raise RuntimeError(f"YouTube API {r.status_code}: {r.text[:200]}")
    items = r.json().get("items", [])
    return items[0] if items else {}


def _fetch_transcript(video_id: str) -> dict | None:
    """Fetch transcript using youtube-transcript-api v1. Returns dict or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        api = YouTubeTranscriptApi()
        # Try English first, then any available language
        try:
            t = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            t = api.fetch(video_id)  # any language
        snippets = list(t)
        full_text = " ".join(s.text for s in snippets if s.text.strip())
        # Clean up auto-caption noise: remove [Music], [Applause] etc.
        full_text = re.sub(r"\[[\w\s]+\]", "", full_text)
        full_text = re.sub(r"\s{2,}", " ", full_text).strip()
        return {
            "language":     t.language,
            "language_code": t.language_code,
            "is_generated": int(t.is_generated),
            "full_text":    full_text,
            "word_count":   len(full_text.split()),
        }
    except Exception:
        return None


def enrich_video(video_id: str, platform: str = "youtube") -> dict:
    """Fetch rich metadata + transcript for one video and upsert into DB.
    Returns a summary dict of what was stored."""
    api_key = get_api_key()
    result = {"video_id": video_id, "metadata": False, "transcript": False, "error": None}

    conn = get_db()
    if not conn:
        result["error"] = "DB unavailable"
        return result

    # Ensure new tables exist
    try:
        db_init_schema(conn)
    except Exception:
        pass

    try:
        # ── Rich metadata ────────────────────────────────────────────────────
        if api_key:
            try:
                item = _fetch_youtube_metadata(video_id, api_key)
                if item:
                    snip = item.get("snippet", {})
                    cd   = item.get("contentDetails", {})
                    conn.execute("""
                        INSERT OR REPLACE INTO video_metadata
                            (platform, post_id, title, thumbnail_url, channel_id, channel_title,
                             duration_seconds, description, tags, fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        platform, video_id,
                        snip.get("title"),
                        (snip.get("thumbnails") or {}).get("maxres", {}).get("url")
                            or (snip.get("thumbnails") or {}).get("high", {}).get("url")
                            or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        snip.get("channelId"),
                        snip.get("channelTitle"),
                        _parse_duration(cd.get("duration", "")),
                        snip.get("description"),
                        json.dumps(snip.get("tags") or []),
                        _iso_now(),
                    ))
                    conn.commit()
                    result["metadata"] = True
            except Exception as exc:
                result["metadata_error"] = str(exc)

        # ── Transcript ───────────────────────────────────────────────────────
        td = _fetch_transcript(video_id)
        if td:
            summary = _extractive_summary(td["full_text"]) if td["full_text"] else ""
            conn.execute("""
                INSERT OR REPLACE INTO transcripts
                    (platform, post_id, language, language_code, is_generated,
                     full_text, summary, word_count, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                platform, video_id,
                td["language"], td["language_code"], td["is_generated"],
                td["full_text"], summary, td["word_count"],
                _iso_now(),
            ))
            conn.commit()
            result["transcript"] = True
            result["transcript_words"] = td["word_count"]
            result["language"] = td["language"]
        else:
            result["transcript_error"] = "No transcript available (disabled or not found)"

    finally:
        conn.close()

    return result


@app.route("/api/enrich/youtube/<video_id>", methods=["POST"])
def api_enrich_youtube(video_id):
    """Fetch and store rich metadata + transcript for one YouTube video."""
    return jsonify(enrich_video(video_id, platform="youtube"))


@app.route("/api/enrich/youtube/batch", methods=["POST"])
def api_enrich_youtube_batch():
    """Enrich multiple videos. Body: {"video_ids": [...]}"""
    data = request.json or {}
    video_ids = data.get("video_ids", [])
    results = [enrich_video(vid, "youtube") for vid in video_ids[:20]]
    return jsonify({"results": results, "count": len(results)})


@app.route("/api/metadata/youtube/<video_id>")
def api_get_metadata(video_id):
    """Return stored metadata + transcript for one video."""
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        meta = conn.execute(
            "SELECT * FROM video_metadata WHERE platform='youtube' AND post_id=?", (video_id,)
        ).fetchone()
        trans = conn.execute(
            "SELECT language, language_code, is_generated, summary, word_count, fetched_at FROM transcripts WHERE platform='youtube' AND post_id=?",
            (video_id,)
        ).fetchone()
        return jsonify({
            "metadata":   row_to_dict(meta),
            "transcript": row_to_dict(trans),
        })
    finally:
        conn.close()


# ── TikTok search (hashtag discovery via clockworks/tiktok-scraper) ────────────

TIKTOK_DISCOVERY_ACTOR = "GdWCkxBtKWOsKjdch"   # clockworks/tiktok-scraper
TIKTOK_COMMENTS_ACTOR  = "BDec00yAmCm1QbMEI"    # clockworks/tiktok-comments-scraper


def _connector_actor(connector_id: str, fallback: str) -> str:
    try:
        spec = _TOSMOD_CFG.connectors.get("connectors", {}).get(connector_id, {})
        actor = spec.get("actor")
        return str(actor).strip() if actor else fallback
    except Exception:
        return fallback


INSTAGRAM_APIFY_ACTOR = _connector_actor("apify_instagram", "apify/instagram-scraper")


def _get_apify_client():
    key = os.environ.get("APIFY_API_KEY")
    if not key:
        return None, "APIFY_API_KEY not set in .env"
    try:
        from apify_client import ApifyClient
        return ApifyClient(key), None
    except ImportError:
        return None, "apify-client not installed — run: pip install apify-client"


def _apify_int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def _coalesce_text(item: dict, keys: list[str]) -> str:
    for k in keys:
        val = item.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


@app.route("/api/search/tiktok")
def api_search_tiktok():
    """Discover TikTok videos from hashtag(s) — metadata only, no comments (fast)."""
    raw = request.args.get("hashtags", "").strip()
    if not raw:
        return jsonify({"error": "hashtags query param required"}), 400

    hashtags  = [h.lstrip("#").strip() for h in raw.replace(",", " ").split() if h.strip()]
    max_posts = max(1, min(int(request.args.get("max_posts", 10)), 50))
    min_views = max(0, int(request.args.get("min_views", 0)))

    client, err = _get_apify_client()
    if err:
        return jsonify({"error": err}), 500

    run_input = {
        "hashtags":           hashtags,
        "resultsPerPage":     max_posts,
        "commentsPerPost":    0,        # metadata-only pass — fast
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
    }
    app.logger.info("TikTok discovery: hashtags=%s max=%s", hashtags, max_posts)

    try:
        run = client.actor(TIKTOK_DISCOVERY_ACTOR).call(run_input=run_input)
    except Exception as exc:
        return jsonify({"error": f"Apify run failed: {exc}"}), 500

    app.logger.info("TikTok discovery run %s status=%s", run.get("id"), run.get("status"))

    if run.get("status") not in ("SUCCEEDED",) and not run.get("defaultDatasetId"):
        return jsonify({
            "error": f"Apify run {run.get('status')}: {run.get('statusMessage') or 'no details'}",
            "run_id": run.get("id"),
        }), 500

    results = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        if not isinstance(item, dict):
            continue
        post_id = str(item.get("id") or item.get("videoId") or "")
        if not post_id:
            continue
        author_meta = item.get("authorMeta") or {}
        stats_raw   = item.get("stats") or item.get("statsV2") or {}
        handle      = author_meta.get("name") or author_meta.get("uniqueId") or ""
        video_meta  = item.get("videoMeta") or {}
        cover_url   = video_meta.get("coverUrl") or video_meta.get("cover") or None

        results.append({
            "id":           post_id,
            "url":          item.get("webVideoUrl") or f"https://www.tiktok.com/@{handle}/video/{post_id}",
            "caption":      (item.get("text") or "")[:120],
            "author":       handle,
            "views":        _apify_int(stats_raw.get("playCount")),
            "likes":        _apify_int(stats_raw.get("diggCount")),
            "comments_count": _apify_int(stats_raw.get("commentCount")),
            "cover_url":    cover_url,
            "in_db":        False,
        })

    if min_views > 0:
        results = [r for r in results if r.get("views", 0) >= min_views]

    # Mark which are already in DB
    conn = get_db()
    if conn:
        try:
            in_db = {r["post_id"] for r in conn.execute(
                "SELECT post_id FROM posts WHERE platform='tiktok'"
            ).fetchall()}
            for r in results:
                r["in_db"] = r["id"] in in_db
        finally:
            conn.close()

    _log_search_run(
        "tiktok",
        raw,
        {
            "hashtags": hashtags,
            "max_posts": max_posts,
            "min_views": min_views,
            "run_id": run.get("id"),
            "status": run.get("status"),
        },
        [r["id"] for r in results],
    )

    return jsonify({
        "results":  results,
        "total":    len(results),
        "hashtags": hashtags,
        "run_id":   run.get("id"),
        "status":   run.get("status"),
    })


@app.route("/api/search/instagram")
def api_search_instagram():
    """
    Discover Instagram posts via Apify actor from hashtag(s).
    Query params:
      - hashtags: comma or space separated
      - max_posts: 1..50
    """
    raw = request.args.get("hashtags", "").strip()
    if not raw:
        return jsonify({"error": "hashtags query param required"}), 400
    hashtags = [h.lstrip("#").strip() for h in raw.replace(",", " ").split() if h.strip()]
    max_posts = max(1, min(int(request.args.get("max_posts", 10)), 50))
    client, err = _get_apify_client()
    if err:
        return jsonify({"error": err}), 500

    run_input = {
        "search": hashtags,
        "resultsLimit": max_posts,
        "resultsType": "posts",
        "searchType": "hashtag",
        "addParentData": False,
    }
    try:
        run = client.actor(INSTAGRAM_APIFY_ACTOR).call(run_input=run_input)
    except Exception as exc:
        return jsonify({"error": f"Instagram Apify run failed: {exc}"}), 500

    if run.get("status") not in ("SUCCEEDED",) and not run.get("defaultDatasetId"):
        return jsonify(
            {
                "error": f"Apify run {run.get('status')}: {run.get('statusMessage') or 'no details'}",
                "run_id": run.get("id"),
            }
        ), 500

    results = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        if not isinstance(item, dict):
            continue
        shortcode = str(
            item.get("shortCode")
            or item.get("shortcode")
            or item.get("id")
            or item.get("postId")
            or ""
        ).strip()
        if not shortcode:
            continue
        owner = item.get("ownerUsername") or item.get("ownerUsername") or item.get("owner")
        if isinstance(owner, dict):
            owner = owner.get("username") or owner.get("id") or ""
        caption = _coalesce_text(item, ["caption", "text", "title"])
        thumb = _coalesce_text(item, ["displayUrl", "thumbnailUrl", "imageUrl"])
        stats = item.get("likesCount") or item.get("likes") or 0
        cmts = item.get("commentsCount") or item.get("comments") or 0
        post_url = _coalesce_text(item, ["url", "postUrl"]) or f"https://www.instagram.com/p/{shortcode}/"
        results.append(
            {
                "id": shortcode,
                "url": post_url,
                "title": caption[:120] if caption else "(no caption)",
                "channel": f"@{owner}" if owner else "@instagram",
                "views": 0,
                "likes": _apify_int(stats),
                "comments_count": _apify_int(cmts),
                "thumbnail": thumb,
                "in_db": _post_exists("instagram", post_id=shortcode, url=post_url),
            }
        )

    _log_search_run(
        "instagram",
        raw,
        {"hashtags": hashtags, "max_posts": max_posts, "run_id": run.get("id"), "status": run.get("status")},
        [r["id"] for r in results],
    )
    return jsonify({"results": results, "total": len(results), "hashtags": hashtags, "run_id": run.get("id")})


# ── TikTok collect one video (comments via BDec00yAmCm1QbMEI) ─────────────────

def _collect_tiktok_comments_apify_once(post_url: str, max_comments: int) -> list:
    """Single Apify run for one post URL. Raises on failure."""
    client, err = _get_apify_client()
    if err:
        raise RuntimeError(err)

    run_input = {
        "postURLs":             [post_url],
        "commentsPerPost":      min(max_comments, 500),
        "maxRepliesPerComment": 0,
    }
    app.logger.info("TikTok comments: url=%s max=%s", post_url, max_comments)

    run = client.actor(TIKTOK_COMMENTS_ACTOR).call(run_input=run_input)
    app.logger.info("TikTok comments run %s status=%s msg=%s",
                    run.get("id"), run.get("status"), run.get("statusMessage"))

    if run.get("status") not in ("SUCCEEDED",) and not run.get("defaultDatasetId"):
        raise RuntimeError(
            f"Apify comments run {run.get('status')}: "
            f"{run.get('statusMessage') or 'no details'}"
        )

    comments = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        if not isinstance(item, dict):
            continue
        cid   = str(item.get("id") or item.get("cid") or len(comments))
        text  = item.get("text") or item.get("commentText") or ""
        ameta = item.get("authorMeta") or item.get("user") or {}
        digg  = _apify_int(item.get("diggCount") or item.get("likeCount"))
        has_gif, gif_url_c, gif_id_c = extract_apify_tiktok_comment_sticker(item)
        if gif_id_c == "":
            gif_id_c = None
        parent = item.get("parentCommentId") or item.get("parent_comment_id")
        comments.append({
            "cid":                 cid,
            "text":                str(text)[:5000],
            "digg_count":          digg,
            "reply_comment_total": _apify_int(item.get("replyCount") or item.get("reply_count")),
            "create_time":         item.get("createTime") or item.get("create_time"),
            "parent_comment_id":   str(parent) if parent else None,
            "aweme_id":            "",
            "user": {"uniqueId": ameta.get("name") or ameta.get("uniqueId") or ""},
            "has_gif":             has_gif,
            "gif_url":             gif_url_c,
            "gif_id":              gif_id_c,
        })
    return comments


def _is_timeout_like_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in ("ReadTimeout", "ConnectTimeout", "TimeoutException", "PoolTimeout"):
        return True
    if isinstance(exc, TimeoutError):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("timeout", "timed out", "read timed out", "deadline"))


def _collect_tiktok_comments_apify(post_url: str, max_comments: int) -> list:
    """Run Apify comments actor with retries on timeout (same video, do not advance)."""
    max_attempts = 3
    wait_sec = 30
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _collect_tiktok_comments_apify_once(post_url, max_comments)
        except BaseException as exc:
            last_exc = exc
            if not _is_timeout_like_error(exc) or attempt >= max_attempts:
                raise
            app.logger.warning(
                "TikTok comments Apify timeout/transient error (attempt %s/%s): %s — waiting %ss before retry same URL",
                attempt, max_attempts, exc, wait_sec,
            )
            time.sleep(wait_sec)
    assert last_exc is not None
    raise last_exc


@app.route("/api/collect/tiktok/url", methods=["POST"])
def api_collect_tiktok_url():
    """Collect post metadata + comments for one TikTok video."""
    data = request.json or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    stratum      = data.get("stratum") or None
    mode         = data.get("mode", "post+comments")
    max_comments = int(data.get("max_comments", 100))
    search_query = data.get("search_query", "")
    m = re.search(r"/video/(\d+)", url)
    pre_post_id = m.group(1) if m else None
    if _post_exists("tiktok", post_id=pre_post_id, url=url):
        return jsonify({
            "status": "skipped",
            "reason_code": "already_exists",
            "reason": "Post already exists in database.",
            "message": "Skipped: this TikTok URL is already collected.",
            "post_id": pre_post_id,
        }), 200
    prior_failure = _has_recent_failure("tiktok", None, url)
    if prior_failure:
        pf = row_to_dict(prior_failure)
        return jsonify({
            "status": "skipped",
            "reason_code": pf.get("reason_code"),
            "reason": pf.get("reason_detail"),
            "message": "Skipped: this post URL has a prior non-collectable failure log.",
        }), 409

    # ── Step 1: post metadata (Playwright/httpx via run_pipeline post-only) ──
    try:
        unified_post, _ = run_pipeline(
            "tiktok", url, "post-only",
            collection_stratum=stratum,
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "detail": traceback.format_exc()}), 500

    if unified_post is None:
        _log_ingestion_failure(
            "tiktok",
            None,
            url,
            "post_unavailable",
            "No post metadata returned for TikTok URL.",
            json.dumps({"mode": mode, "max_comments": max_comments}),
        )
        return jsonify({
            "error": "No post metadata. URL must be the full canonical form: "
                     "https://www.tiktok.com/@username/video/VIDEO_ID"
        }), 404

    post_id = unified_post.post_id
    # Metadata fallback: TikTok oEmbed often provides caption/title + thumbnail
    # when Playwright/httpx post parsing is sparse.
    try:
        meta = _fetch_tiktok_oembed_meta(url)
        connm = get_db()
        if connm:
            try:
                # Fill caption when missing/blank.
                if (not (unified_post.caption or "").strip()) and meta.get("title"):
                    connm.execute(
                        "UPDATE posts SET caption=? WHERE platform='tiktok' AND post_id=?",
                        (meta["title"], post_id),
                    )
                _upsert_video_metadata_row(
                    connm,
                    "tiktok",
                    post_id,
                    title=meta.get("title", ""),
                    thumbnail_url=meta.get("thumbnail_url", ""),
                    channel_title=meta.get("author_name", ""),
                    description=meta.get("title", ""),
                )
                connm.commit()
            finally:
                connm.close()
    except Exception:
        pass

    # ── Step 2: comments via Apify comments scraper ───────────────────────────
    gif_count = 0
    stickers_archived = 0
    n_comments = 0
    if mode == "post+comments":
        try:
            from thesis_scraper.main import assign_thread_order
            from thesis_scraper.processors.standardizer import standardize_comment
            from thesis_scraper.storage.database import get_connection, init_schema, insert_comment

            raw_comments = _collect_tiktok_comments_apify(url, max_comments)
            for c in raw_comments:
                c["aweme_id"] = post_id   # patch in the resolved ID

            ordered = assign_thread_order(raw_comments)
            try:
                import yaml
                cfg_path = PROJECT_ROOT / "thesis_scraper" / "config" / "settings.yaml"
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            except Exception:
                cfg = {}
            salt    = os.environ.get("ANONYMIZATION_SALT") or cfg.get("anonymization", {}).get("salt", "thesis_salt")
            db_path = os.environ.get("TOSMOD_DB_PATH") or os.environ.get("THESIS_DB_PATH") or str(DEFAULT_DB)
            scraped_at = _iso_now()

            conn2 = get_connection(db_path)
            init_schema(conn2)
            for i, c in enumerate(ordered):
                try:
                    uc = standardize_comment(
                        c, "tiktok", post_id=post_id,
                        depth=c.get("depth", 0),
                        thread_position=c.get("thread_position", i),
                        thread_id=c.get("thread_id"),
                        order_in_thread=c.get("order_in_thread", 0),
                        salt=salt,
                        scraped_at_iso=scraped_at,
                    )
                    if uc.has_gif and uc.gif_url:
                        local_rel = _download_tiktok_comment_sticker_archive(
                            uc.gif_url, post_id, uc.comment_id
                        )
                        if local_rel:
                            uc.gif_local_path = local_rel
                            stickers_archived += 1
                    insert_comment(conn2, uc, "tiktok", post_id)
                    if uc.has_gif:
                        gif_count += 1
                    n_comments += 1
                except Exception:
                    pass
            conn2.close()
        except Exception as exc:
            app.logger.error("TikTok comments collection failed for %s: %s", url, exc)
            _log_ingestion_failure(
                "tiktok",
                post_id,
                url,
                "collect_error",
                str(exc),
                json.dumps({"mode": mode, "max_comments": max_comments}),
            )
            # Return partial success — post was saved, comments failed
            return jsonify({
                "status":               "partial",
                "post_id":              post_id,
                "comments_collected":     0,
                "gif_comments":         0,
                "stickers_archived":    0,
                "mode":                 mode,
                "comments_error":       str(exc),
            })

    if mode != "post-only" and n_comments == 0:
        _log_ingestion_failure(
            "tiktok",
            post_id,
            url,
            "zero_comments",
            "Collected zero comments or comments are restricted/disabled.",
            json.dumps({"mode": mode, "max_comments": max_comments, "search_query": search_query}),
        )
        _delete_post_bundle("tiktok", post_id)
        return jsonify({
            "status": "dropped",
            "post_id": post_id,
            "reason_code": "zero_comments",
            "reason": "Collected zero comments or comments are restricted/disabled.",
        }), 200

    if search_query:
        conn = get_db()
        if conn:
            try:
                conn.execute(
                    "UPDATE posts SET search_query=? WHERE platform='tiktok' AND post_id=?",
                    (search_query, post_id),
                )
                conn.commit()
            finally:
                conn.close()

    return jsonify({
        "status":               "ok",
        "post_id":              post_id,
        "comments_collected":   n_comments,
        "gif_comments":         gif_count,
        "stickers_archived":    stickers_archived,
        "mode":                 mode,
    })


@app.route("/api/collect/instagram/url", methods=["POST"])
def api_collect_instagram_url():
    """
    Collect Instagram post metadata/comments via Apify actor.
    Body: {url, max_comments, stratum, search_query}
    """
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url or "instagram.com" not in url:
        return jsonify({"error": "Valid instagram.com post URL required"}), 400
    max_comments = max(1, min(int(data.get("max_comments", 100)), 500))
    stratum = data.get("stratum") or None
    search_query = (data.get("search_query") or "").strip()

    shortcode = _extract_instagram_shortcode(url)
    if _post_exists("instagram", post_id=shortcode, url=url):
        return jsonify(
            {
                "status": "skipped",
                "reason_code": "already_exists",
                "reason": "Post already exists in database.",
            }
        ), 200

    client, err = _get_apify_client()
    if err:
        return jsonify({"error": err}), 500

    run_input = {
        "directUrls": [url],
        "resultsLimit": 1,
        "resultsType": "posts",
        "searchType": "url",
        "addParentData": True,
    }
    try:
        run = client.actor(INSTAGRAM_APIFY_ACTOR).call(run_input=run_input)
    except Exception as exc:
        return jsonify({"error": f"Instagram Apify run failed: {exc}"}), 500
    if run.get("status") not in ("SUCCEEDED",) and not run.get("defaultDatasetId"):
        return jsonify({"error": f"Apify run failed: {run.get('status')}"}), 500

    rows = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    if not rows:
        return jsonify({"error": "No data returned from Apify actor"}), 404
    item = rows[0] if isinstance(rows[0], dict) else {}

    post_id = str(
        item.get("shortCode")
        or item.get("shortcode")
        or item.get("id")
        or shortcode
        or uuid.uuid4().hex[:12]
    )
    caption = _coalesce_text(item, ["caption", "text", "title"])
    owner = item.get("ownerUsername") or item.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("username") or owner.get("id") or ""
    thumb = _coalesce_text(item, ["displayUrl", "thumbnailUrl", "imageUrl"])
    likes = _apify_int(item.get("likesCount") or item.get("likes"))
    comments_count = _apify_int(item.get("commentsCount") or item.get("comments"))

    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        scraped_at = _iso_now()
        conn.execute(
            """
            INSERT INTO posts (
              platform, post_id, url, author_id, caption, posted_at, views, likes,
              shares, comments_count, scraped_at, collection_stratum, search_query,
              post_source, comments_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, post_id) DO UPDATE SET
              caption=excluded.caption,
              likes=excluded.likes,
              comments_count=excluded.comments_count,
              search_query=COALESCE(excluded.search_query, posts.search_query)
            """,
            (
                "instagram",
                post_id,
                url,
                str(owner or "instagram_apify"),
                caption,
                scraped_at,
                0,
                likes,
                0,
                comments_count,
                scraped_at,
                stratum or "search_term",
                search_query or "instagram_url",
                "instagram_apify",
                "instagram_apify",
            ),
        )
        conn.execute(
            """
            INSERT INTO video_metadata
            (platform, post_id, title, thumbnail_url, channel_id, channel_title, duration_seconds, description, tags, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, post_id) DO UPDATE SET
              title=excluded.title, thumbnail_url=excluded.thumbnail_url, description=excluded.description
            """,
            (
                "instagram",
                post_id,
                caption[:180] if caption else "",
                thumb,
                "",
                str(owner or ""),
                None,
                caption,
                json.dumps([], ensure_ascii=False),
                scraped_at,
            ),
        )
        # Comments are actor-dependent; accept comments arrays from common keys.
        raw_comments = item.get("latestComments") or item.get("comments") or []
        if isinstance(raw_comments, list):
            for i, c in enumerate(raw_comments[:max_comments]):
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id") or c.get("pk") or f"igc_{post_id}_{i}")
                text = _coalesce_text(c, ["text", "comment", "content"])[:5000]
                if not text:
                    continue
                author = c.get("ownerUsername") or c.get("username") or c.get("owner")
                if isinstance(author, dict):
                    author = author.get("username") or author.get("id") or "unknown"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO comments
                    (platform, post_id, comment_id, parent_comment_id, author_id, text,
                     posted_at, likes, reply_count, depth, thread_position, thread_id,
                     order_in_thread, platform_raw_timestamp, raw_json, scraped_at, has_gif,
                     gif_url, gif_id, gif_local_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "instagram",
                        post_id,
                        cid,
                        None,
                        str(author or "unknown"),
                        text,
                        str(c.get("timestamp") or c.get("createdAt") or scraped_at),
                        _apify_int(c.get("likesCount") or c.get("likes")),
                        _apify_int(c.get("repliesCount") or c.get("replyCount")),
                        0,
                        i,
                        cid,
                        0,
                        "",
                        json.dumps(c, ensure_ascii=False),
                        scraped_at,
                        0,
                        None,
                        None,
                        None,
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    return jsonify(
        {
            "status": "ok",
            "post_id": post_id,
            "comments_collected": min(max_comments, len(raw_comments) if isinstance(raw_comments, list) else 0),
            "mode": "post+comments",
        }
    )


# ── Annotation API ──────────────────────────────────────────────────────────────

ANNOTATION_LABELS = set(_TOSMOD_CFG.label_names())
ANNOTATION_MODALITIES = set(_TOSMOD_CFG.modalities())


def _annotation_progress(conn, where_clause: str = "", params: tuple = ()) -> dict:
    """Count comments matching filters; ``where_clause`` uses ``p.`` for posts (stratum, search_query)."""
    join_posts = "JOIN posts p ON p.platform = c.platform AND p.post_id = c.post_id"
    total = conn.execute(
        f"SELECT COUNT(*) FROM comments c {join_posts} {where_clause}",
        params,
    ).fetchone()[0]
    done = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM comments c
        {join_posts}
        JOIN annotations a
          ON a.platform=c.platform AND a.post_id=c.post_id AND a.comment_id=c.comment_id
        {where_clause}
        """,
        params,
    ).fetchone()[0]
    pct = round((done / total * 100.0), 2) if total else 0.0
    return {"total": total, "done": done, "pct": pct}


def _infer_modality(row: sqlite3.Row) -> str:
    has_gif = int(row["has_gif"] or 0) == 1
    text = (row["text"] or "").strip()
    if has_gif and text:
        return "text+gif"
    if has_gif:
        return "gif"
    return "text"


def _extract_comment_image_url(row: sqlite3.Row) -> str | None:
    """Best-effort extraction of non-gif image URL from comment raw_json."""
    raw = row["raw_json"] if "raw_json" in row.keys() else None
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    # Common keys observed across extension/APIs
    candidates = [
        obj.get("image_url"),
        obj.get("imageUrl"),
        obj.get("media_url"),
        obj.get("mediaUrl"),
        obj.get("comment_image_url"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    media = obj.get("Comment Media")
    if isinstance(media, str) and media.strip() and media.strip().lower() != "not available":
        return media.strip()
    return None


def _extract_post_thumbnail_url(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        obj = json.loads(raw_json)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    candidates = [
        obj.get("thumbnail_url"),
        obj.get("thumbnailUrl"),
        obj.get("cover_url"),
        obj.get("coverUrl"),
        ((obj.get("videoMeta") or {}).get("coverUrl") if isinstance(obj.get("videoMeta"), dict) else None),
        (((obj.get("thumbnails") or {}).get("high") or {}).get("url") if isinstance(obj.get("thumbnails"), dict) else None),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _annotation_thumbnail_url(platform: str, post_id: str, stored_thumb: str | None) -> str:
    """Return a thumbnail URL suitable for the annotate UI (handles expired IG CDN links)."""
    plat = (platform or "").strip().lower()
    pid = (post_id or "").strip()
    if plat == "instagram" and pid:
        return f"/api/instagram-thumbnail/{pid}"
    return (stored_thumb or "").strip()


def _build_annotation_where(filters: dict) -> tuple[str, list]:
    clauses = []
    params: list = []
    platform = (filters.get("platform") or "").strip()
    stratum = (filters.get("stratum") or "").strip()
    sub_tag = (filters.get("sub_tag") or "").strip()
    post_id = (filters.get("post_id") or "").strip()
    has_gif_only = str(filters.get("has_gif_only", "")).lower() in ("1", "true", "yes")
    if platform:
        clauses.append("p.platform=?")
        params.append(platform)
    if stratum:
        clauses.append("COALESCE(p.collection_stratum,'')=?")
        params.append(stratum)
    if sub_tag:
        clauses.append("COALESCE(p.search_query,'')=?")
        params.append(sub_tag)
    if post_id:
        clauses.append("p.post_id=?")
        params.append(post_id)
    if has_gif_only:
        clauses.append("COALESCE(c.has_gif,0)=1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


@app.route("/api/annotate/stats")
def api_annotate_stats():
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        where, params = _build_annotation_where(request.args)
        progress = _annotation_progress(
            conn,
            where_clause=where,
            params=tuple(params),
        )
        by_platform_rows = conn.execute(
            """
            SELECT c.platform,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.comment_id IS NOT NULL THEN 1 ELSE 0 END) AS done
            FROM comments c
            JOIN posts p ON p.platform=c.platform AND p.post_id=c.post_id
            LEFT JOIN annotations a
              ON a.platform=c.platform AND a.post_id=c.post_id AND a.comment_id=c.comment_id
            {where}
            GROUP BY c.platform
            ORDER BY c.platform
            """.format(where=where),
            tuple(params),
        ).fetchall()
        by_label_rows = conn.execute(
            """
            SELECT label, COUNT(*) AS count
            FROM annotations
            GROUP BY label
            ORDER BY count DESC
            """
        ).fetchall()
        goal_rows = conn.execute(
            """
            SELECT c.platform AS platform,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.comment_id IS NOT NULL THEN 1 ELSE 0 END) AS done
            FROM comments c
            JOIN posts p ON p.platform = c.platform AND p.post_id = c.post_id
            LEFT JOIN annotations a
              ON a.platform = c.platform AND a.post_id = c.post_id AND a.comment_id = c.comment_id
            WHERE c.platform IN ('youtube', 'tiktok', 'instagram')
            GROUP BY c.platform
            ORDER BY c.platform
            """
        ).fetchall()
        by_platform = []
        for r in by_platform_rows:
            total = int(r["total"] or 0)
            done = int(r["done"] or 0)
            by_platform.append({
                "platform": r["platform"],
                "total": total,
                "done": done,
                "pct": round((done / total * 100.0), 2) if total else 0.0,
            })
        goal_by_platform = []
        goal = ANNOTATE_MANUAL_GOAL_COMMENTS_PER_PLATFORM
        for r in goal_rows:
            corp = int(r["total"] or 0)
            ann = int(r["done"] or 0)
            goal_by_platform.append({
                "platform": r["platform"],
                "corpus_comments": corp,
                "annotated": ann,
                "goal": goal,
                "goal_remaining": max(0, goal - ann),
                "toward_goal_pct": round(min(ann / goal * 100.0, 100.0), 2) if goal else 0.0,
            })
        by_label = [{"label": r["label"], "count": r["count"]} for r in by_label_rows]
        return jsonify({
            "progress": progress,
            "by_platform": by_platform,
            "by_label": by_label,
            "annotate_goal_per_platform": goal,
            "goal_by_platform": goal_by_platform,
        })
    finally:
        conn.close()


@app.route("/api/annotate/context/<platform>/<post_id>", methods=["GET", "POST"])
def api_annotate_context(platform, post_id):
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    force = request.method == "POST" or request.args.get("refresh") in ("1", "true")
    try:
        vm = conn.execute(
            """
            SELECT title, channel_title, description, annotation_context
            FROM video_metadata
            WHERE platform=? AND post_id=?
            """,
            (platform, post_id),
        ).fetchone()
        tr = conn.execute(
            """
            SELECT summary
            FROM transcripts
            WHERE platform=? AND post_id=?
            """,
            (platform, post_id),
        ).fetchone()
        post = conn.execute(
            "SELECT caption FROM posts WHERE platform=? AND post_id=?",
            (platform, post_id),
        ).fetchone()
        if vm and vm["annotation_context"] and not force:
            return jsonify({"annotation_context": vm["annotation_context"], "cached": True})

        parts = []
        if vm and vm["title"]:
            parts.append(vm["title"])
        if post and post["caption"]:
            parts.append(post["caption"])
        if tr and tr["summary"]:
            parts.append(tr["summary"])
        merged = " ".join(p.strip() for p in parts if p and str(p).strip())
        annotation_context = _extractive_summary(merged, max_words=80) if merged else ""
        if not annotation_context:
            annotation_context = (merged[:320] + "...") if len(merged) > 320 else merged

        conn.execute(
            """
            INSERT OR REPLACE INTO video_metadata (
                platform, post_id, title, thumbnail_url, channel_id, channel_title,
                duration_seconds, description, tags, fetched_at, annotation_context
            )
            SELECT
                platform, post_id, title, thumbnail_url, channel_id, channel_title,
                duration_seconds, description, tags, fetched_at, ?
            FROM video_metadata
            WHERE platform=? AND post_id=?
            """,
            (annotation_context, platform, post_id),
        )
        conn.commit()
        return jsonify({"annotation_context": annotation_context, "cached": False})
    finally:
        conn.close()


@app.route("/api/annotate/next")
def api_annotate_next():
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        where, params = _build_annotation_where(request.args)
        next_where = "WHERE a.comment_id IS NULL"
        if where:
            next_where = where + " AND a.comment_id IS NULL"
        shuffle = str(request.args.get("shuffle", "")).lower() in ("1", "true", "yes", "on")
        order_by = "RANDOM()" if shuffle else "c.scraped_at ASC, c.platform ASC, c.post_id ASC, c.thread_position ASC"
        row = conn.execute(
            """
            SELECT
              c.platform, c.post_id, c.comment_id, c.text, c.has_gif, c.gif_url,
              c.depth, c.parent_comment_id, c.posted_at, c.likes, c.raw_json,
              p.url, p.collection_stratum, p.raw_json AS post_raw_json,
              vm.title, vm.thumbnail_url, vm.channel_title, vm.annotation_context
            FROM comments c
            JOIN posts p ON p.platform=c.platform AND p.post_id=c.post_id
            LEFT JOIN annotations a
              ON a.platform=c.platform AND a.post_id=c.post_id AND a.comment_id=c.comment_id
            LEFT JOIN video_metadata vm
              ON vm.platform=c.platform AND vm.post_id=c.post_id
            {where}
            ORDER BY {order_by}
            LIMIT 1
            """.format(where=next_where, order_by=order_by),
            tuple(params),
        ).fetchone()
        progress = _annotation_progress(conn, where_clause=where, params=tuple(params))
        if not row:
            return jsonify({"done": True, "progress": progress})

        parent_text = None
        if row["parent_comment_id"]:
            pr = conn.execute(
                """
                SELECT text FROM comments
                WHERE platform=? AND post_id=? AND comment_id=?
                """,
                (row["platform"], row["post_id"], row["parent_comment_id"]),
            ).fetchone()
            if pr:
                parent_text = pr["text"]
        modality = _infer_modality(row)
        image_url = _extract_comment_image_url(row)
        thumb = _annotation_thumbnail_url(
            row["platform"],
            row["post_id"],
            row["thumbnail_url"] or _extract_post_thumbnail_url(row["post_raw_json"]),
        )
        return jsonify({
            "done": False,
            "comment": {
                "platform": row["platform"],
                "post_id": row["post_id"],
                "comment_id": row["comment_id"],
                "text": row["text"],
                "has_gif": int(row["has_gif"] or 0) == 1,
                "gif_url": row["gif_url"],
                "image_url": image_url,
                "depth": row["depth"],
                "parent_text": parent_text,
                "posted_at": row["posted_at"],
                "likes": row["likes"],
                "modality_default": modality,
                "collection_stratum": row["collection_stratum"],
            },
            "post": {
                "platform": row["platform"],
                "post_id": row["post_id"],
                "url": row["url"],
                "title": row["title"],
                "thumbnail_url": thumb,
                "channel_title": row["channel_title"],
                "annotation_context": row["annotation_context"],
            },
            "progress": progress,
            "filters": {
                "platform": request.args.get("platform", ""),
                "stratum": request.args.get("stratum", ""),
                "sub_tag": request.args.get("sub_tag", ""),
                "post_id": request.args.get("post_id", ""),
                "has_gif_only": request.args.get("has_gif_only", ""),
                "shuffle": request.args.get("shuffle", ""),
            },
        })
    finally:
        conn.close()


@app.route("/api/annotate/item")
def api_annotate_item():
    platform = request.args.get("platform", "").strip()
    post_id = request.args.get("post_id", "").strip()
    comment_id = request.args.get("comment_id", "").strip()
    if not platform or not post_id or not comment_id:
        return jsonify({"error": "platform, post_id, comment_id required"}), 400
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        row = conn.execute(
            """
            SELECT
              c.platform, c.post_id, c.comment_id, c.text, c.has_gif, c.gif_url, c.raw_json,
              c.depth, c.parent_comment_id, c.posted_at, c.likes,
              p.url, p.collection_stratum, p.raw_json AS post_raw_json,
              vm.title, vm.thumbnail_url, vm.channel_title, vm.annotation_context,
              a.label, a.severity, a.modality, a.harmful, a.uncertain, a.gif_context, a.annotated_at,
              a.label_source, a.split
            FROM comments c
            JOIN posts p ON p.platform=c.platform AND p.post_id=c.post_id
            LEFT JOIN video_metadata vm ON vm.platform=c.platform AND vm.post_id=c.post_id
            LEFT JOIN annotations a ON a.platform=c.platform AND a.post_id=c.post_id AND a.comment_id=c.comment_id
            WHERE c.platform=? AND c.post_id=? AND c.comment_id=?
            LIMIT 1
            """,
            (platform, post_id, comment_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Comment not found"}), 404
        parent_text = None
        if row["parent_comment_id"]:
            pr = conn.execute(
                "SELECT text FROM comments WHERE platform=? AND post_id=? AND comment_id=?",
                (row["platform"], row["post_id"], row["parent_comment_id"]),
            ).fetchone()
            if pr:
                parent_text = pr["text"]
        modality_default = _infer_modality(row)
        image_url = _extract_comment_image_url(row)
        thumb = _annotation_thumbnail_url(
            row["platform"],
            row["post_id"],
            row["thumbnail_url"] or _extract_post_thumbnail_url(row["post_raw_json"]),
        )
        return jsonify({
            "comment": {
                "platform": row["platform"],
                "post_id": row["post_id"],
                "comment_id": row["comment_id"],
                "text": row["text"],
                "has_gif": int(row["has_gif"] or 0) == 1,
                "gif_url": row["gif_url"],
                "image_url": image_url,
                "depth": row["depth"],
                "parent_text": parent_text,
                "posted_at": row["posted_at"],
                "likes": row["likes"],
                "modality_default": modality_default,
                "collection_stratum": row["collection_stratum"],
            },
            "post": {
                "platform": row["platform"],
                "post_id": row["post_id"],
                "url": row["url"],
                "title": row["title"],
                "thumbnail_url": thumb,
                "channel_title": row["channel_title"],
                "annotation_context": row["annotation_context"],
            },
            "annotation": {
                "label": row["label"],
                "severity": row["severity"],
                "modality": row["modality"],
                "harmful": row["harmful"],
                "uncertain": row["uncertain"],
                "gif_context": row["gif_context"],
                "annotated_at": row["annotated_at"],
                "label_source": row["label_source"],
                "split": row["split"],
            },
        })
    finally:
        conn.close()


@app.route("/api/annotate/save", methods=["POST"])
def api_annotate_save():
    data = request.json or {}
    platform = data.get("platform")
    post_id = data.get("post_id")
    comment_id = data.get("comment_id")
    label = (data.get("label") or "").strip().upper()
    severity = data.get("severity")
    modality = (data.get("modality") or "").strip().lower()
    uncertain = 1 if data.get("uncertain") else 0
    gif_context = (data.get("gif_context") or "").strip()
    if not platform or not post_id or not comment_id:
        return jsonify({"error": "platform, post_id, comment_id required"}), 400
    if label not in ANNOTATION_LABELS:
        return jsonify({"error": f"Invalid label: {label}"}), 400
    if modality not in ANNOTATION_MODALITIES:
        return jsonify({"error": f"Invalid modality: {modality}"}), 400
    if label == "SAFE":
        severity = None
    else:
        try:
            severity = int(severity)
        except Exception:
            return jsonify({"error": "severity must be 1/2/3 for harmful labels"}), 400
        if severity not in (1, 2, 3):
            return jsonify({"error": "severity must be 1/2/3"}), 400
    harmful = 0 if label == "SAFE" else 1

    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        split_val = (data.get("split") or "").strip() or None
        if split_val and split_val not in ("train", "val", "test"):
            return jsonify({"error": "split must be train, val, test, or empty"}), 400
        conn.execute(
            """
            INSERT OR REPLACE INTO annotations (
                platform, post_id, comment_id, label, severity,
                modality, harmful, uncertain, gif_context, annotated_at,
                label_source, split
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human', ?)
            """,
            (
                platform, post_id, comment_id, label, severity, modality,
                harmful, uncertain, gif_context, _iso_now(),
                split_val,
            ),
        )
        conn.commit()
        return jsonify({
            "saved": True,
            "annotation": {
                "platform": platform,
                "post_id": post_id,
                "comment_id": comment_id,
                "label": label,
                "severity": severity,
                "modality": modality,
                "harmful": harmful,
                "uncertain": uncertain,
                "gif_context": gif_context,
                "label_source": "human",
                "split": split_val,
            },
        })
    finally:
        conn.close()


@app.route("/api/annotate/history")
def api_annotate_history():
    limit = max(1, min(int(request.args.get("limit", 25)), 100))
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        rows = conn.execute(
            """
            SELECT a.platform, a.post_id, a.comment_id, a.label, a.severity, a.modality,
                   a.harmful, a.uncertain, a.gif_context, a.annotated_at, a.label_source, a.split, c.text
            FROM annotations a
            LEFT JOIN comments c
              ON c.platform=a.platform AND c.post_id=a.post_id AND c.comment_id=a.comment_id
            ORDER BY a.annotated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return jsonify({"history": [row_to_dict(r) for r in rows]})
    finally:
        conn.close()


@app.route("/api/annotate/undo", methods=["POST"])
def api_annotate_undo():
    data = request.json or {}
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        platform = data.get("platform")
        post_id = data.get("post_id")
        comment_id = data.get("comment_id")
        if platform and post_id and comment_id:
            row = conn.execute(
                """
                SELECT platform, post_id, comment_id
                FROM annotations
                WHERE platform=? AND post_id=? AND comment_id=?
                """,
                (platform, post_id, comment_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT platform, post_id, comment_id
                FROM annotations
                ORDER BY annotated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return jsonify({"undone": False, "error": "No annotation to undo"}), 404
        conn.execute(
            """
            DELETE FROM annotations
            WHERE platform=? AND post_id=? AND comment_id=?
            """,
            (row["platform"], row["post_id"], row["comment_id"]),
        )
        conn.commit()
        return jsonify({
            "undone": True,
            "platform": row["platform"],
            "post_id": row["post_id"],
            "comment_id": row["comment_id"],
        })
    finally:
        conn.close()


@app.route("/api/db/tags")
def api_db_tags():
    conn = get_db()
    if not conn:
        return jsonify({"error": "Database not found", "tags": [], "sub_tags": []}), 404
    platform = (request.args.get("platform") or "").strip()
    try:
        pwhere = "WHERE platform=?" if platform else ""
        pparams = (platform,) if platform else ()
        tags = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT COALESCE(collection_stratum,'') FROM posts {pwhere} ORDER BY 1",
                pparams,
            ).fetchall()
            if (r[0] or "").strip()
        ]
        sub_tags = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT COALESCE(search_query,'') FROM posts {pwhere} ORDER BY 1",
                pparams,
            ).fetchall()
            if (r[0] or "").strip()
        ]
        return jsonify({"tags": tags, "sub_tags": sub_tags})
    finally:
        conn.close()


@app.route("/api/delete/by-tag/preview")
def api_delete_by_tag_preview():
    platform = (request.args.get("platform") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    sub_tag = (request.args.get("sub_tag") or "").strip()
    if not (platform or tag or sub_tag):
        return jsonify({"error": "At least one filter required: platform/tag/sub_tag"}), 400
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        clauses = []
        params = []
        if platform:
            clauses.append("platform=?")
            params.append(platform)
        if tag:
            clauses.append("COALESCE(collection_stratum,'')=?")
            params.append(tag)
        if sub_tag:
            clauses.append("COALESCE(search_query,'')=?")
            params.append(sub_tag)
        where_sql = f"WHERE {' AND '.join(clauses)}"
        post_count = conn.execute(
            f"SELECT COUNT(*) FROM posts {where_sql}",
            tuple(params),
        ).fetchone()[0]
        comment_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM comments c
            JOIN posts p ON p.platform=c.platform AND p.post_id=c.post_id
            {where_sql}
            """,
            tuple(params),
        ).fetchone()[0]
        return jsonify({
            "filters": {"platform": platform, "tag": tag, "sub_tag": sub_tag},
            "posts": post_count,
            "comments": comment_count,
        })
    finally:
        conn.close()


@app.route("/api/delete/by-tag", methods=["POST"])
def api_delete_by_tag():
    data = request.json or {}
    if data.get("confirm") != "yes":
        return jsonify({"error": 'Send {"confirm":"yes"} to confirm'}), 400
    platform = (data.get("platform") or "").strip()
    tag = (data.get("tag") or "").strip()
    sub_tag = (data.get("sub_tag") or "").strip()
    if not (platform or tag or sub_tag):
        return jsonify({"error": "At least one filter required: platform/tag/sub_tag"}), 400

    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        clauses = []
        params = []
        if platform:
            clauses.append("platform=?")
            params.append(platform)
        if tag:
            clauses.append("COALESCE(collection_stratum,'')=?")
            params.append(tag)
        if sub_tag:
            clauses.append("COALESCE(search_query,'')=?")
            params.append(sub_tag)
        where_sql = f"WHERE {' AND '.join(clauses)}"

        rows = conn.execute(
            f"SELECT platform, post_id FROM posts {where_sql}",
            tuple(params),
        ).fetchall()
        if not rows:
            return jsonify({"deleted": False, "posts_deleted": 0, "comments_deleted": 0, "message": "No matching posts"})

        posts_deleted = 0
        comments_deleted = 0
        for r in rows:
            p, pid = r["platform"], r["post_id"]
            comments_deleted += conn.execute(
                "DELETE FROM comments WHERE platform=? AND post_id=?",
                (p, pid),
            ).rowcount
            conn.execute("DELETE FROM annotations WHERE platform=? AND post_id=?", (p, pid))
            conn.execute("DELETE FROM video_metadata WHERE platform=? AND post_id=?", (p, pid))
            conn.execute("DELETE FROM transcripts WHERE platform=? AND post_id=?", (p, pid))
            posts_deleted += conn.execute(
                "DELETE FROM posts WHERE platform=? AND post_id=?",
                (p, pid),
            ).rowcount
        conn.commit()
        return jsonify({
            "deleted": True,
            "posts_deleted": posts_deleted,
            "comments_deleted": comments_deleted,
            "filters": {"platform": platform, "tag": tag, "sub_tag": sub_tag},
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


def _extract_instagram_shortcode(value: str) -> str | None:
    if not value:
        return None
    m = re.search(r"/(?:reel|reels|p)/([A-Za-z0-9_-]{11})", value)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|reel_?)([A-Za-z0-9_-]{11})\.json", value, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _fetch_instagram_page_meta(url: str) -> dict:
    if not url:
        return {}
    try:
        with httpx.Client(timeout=12, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return {}
        text = r.text
        title_m = re.search(r'<meta property="og:title" content="([^"]*)"', text, re.IGNORECASE)
        desc_m = re.search(r'<meta property="og:description" content="([^"]*)"', text, re.IGNORECASE)
        img_m = re.search(r'<meta property="og:image" content="([^"]*)"', text, re.IGNORECASE)
        title = html_lib.unescape(title_m.group(1)) if title_m else ""
        description = html_lib.unescape(desc_m.group(1)) if desc_m else ""
        thumbnail_url = html_lib.unescape(img_m.group(1)) if img_m else ""
        return {
            "title": title,
            "description": description,
            "thumbnail_url": thumbnail_url,
            "caption": _normalize_instagram_caption(title or description),
        }
    except Exception:
        return {}


def _normalize_instagram_caption(text: str) -> str:
    """
    Turn OG title/description into a clean caption.
    Example:
      'Neve on Instagram: "Comment who you think..."'
      -> 'Comment who you think...'
    """
    s = html_lib.unescape((text or "").strip())
    if not s:
        return ""
    m = re.match(r'^.+?\s+on\s+Instagram:\s+"(.+)"$', s, flags=re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    # Collapse excessive whitespace without touching emoji.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fetch_instagram_thumbnail_legacy_media(shortcode: str) -> str:
    """
    Legacy fallback:
      https://www.instagram.com/p/<shortcode>/media/?size=l
    Often responds with 302 to a CDN thumbnail URL.
    """
    if not shortcode:
        return ""
    try:
        media_url = f"https://www.instagram.com/p/{shortcode}/media/?size=l"
        with httpx.Client(timeout=12, follow_redirects=False) as c:
            r = c.get(media_url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location") or ""
            return html_lib.unescape(loc.strip())
        return ""
    except Exception:
        return ""


def _fetch_instagram_thumbnail_instaloader(shortcode: str) -> str:
    """
    Fallback thumbnail resolver for Instagram when og:image is unavailable.
    Uses Instaloader's shortcode API (best-effort; returns empty string on failure).
    """
    if not shortcode:
        return ""
    try:
        import instaloader
        loader = instaloader.Instaloader()
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        # Some versions expose video_url, others only url for the media thumbnail.
        thumb = getattr(post, "video_url", None) or getattr(post, "url", None) or ""
        return str(thumb or "")
    except Exception:
        return ""


def _fetch_tiktok_oembed_meta(url: str) -> dict:
    """
    Best-effort TikTok metadata fallback via oEmbed.
    Returns: {title, thumbnail_url, author_name}
    """
    if not url:
        return {"_error": "missing_url"}
    try:
        with httpx.Client(timeout=12, follow_redirects=True) as c:
            r = c.get("https://www.tiktok.com/oembed", params={"url": url})
        if r.status_code != 200:
            return {"_error": f"oembed_http_{r.status_code}"}
        data = r.json() if r.headers.get("content-type", "").lower().startswith("application/json") else {}
        if not isinstance(data, dict):
            return {"_error": "oembed_non_json"}
        return {
            "title": (data.get("title") or "").strip(),
            "thumbnail_url": (data.get("thumbnail_url") or "").strip(),
            "author_name": (data.get("author_name") or "").strip(),
        }
    except Exception as exc:
        return {"_error": f"oembed_exception:{type(exc).__name__}"}


def _upsert_video_metadata_row(
    conn: sqlite3.Connection,
    platform: str,
    post_id: str,
    *,
    title: str = "",
    thumbnail_url: str = "",
    channel_title: str = "",
    description: str = "",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO video_metadata
        (platform, post_id, title, thumbnail_url, channel_id, channel_title, duration_seconds, description, tags, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            platform,
            post_id,
            title or "",
            thumbnail_url or "",
            "",
            channel_title or "",
            None,
            description or "",
            json.dumps([], ensure_ascii=False),
            _iso_now(),
        ),
    )


def _guess_media_ext(url: str, default_ext: str = ".bin") -> str:
    try:
        path = urlparse(url).path or ""
    except Exception:
        return default_ext
    ext = Path(path).suffix.lower()
    return ext if ext and len(ext) <= 6 else default_ext


MAX_TIKTOK_STICKER_BYTES = 12 * 1024 * 1024


def _download_tiktok_comment_sticker_archive(gif_url: str, post_id: str, comment_id: str) -> str | None:
    """
    Persist a TikTok comment sticker/GIF from CDN URL to data/raw/tiktok_comment_media/.
    Returns a path relative to the project root (posix slashes), or None on failure.
    CDN URLs expire; local copy supports offline / multimodal training.
    """
    if not gif_url or not str(gif_url).startswith(("http://", "https://")):
        return None
    safe_pid = re.sub(r"[^\w\-]+", "_", str(post_id))[:80]
    safe_cid = re.sub(r"[^\w\-]+", "_", str(comment_id))[:120]
    out_dir = PROJECT_ROOT / "data" / "raw" / "tiktok_comment_media"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _guess_media_ext(gif_url, ".bin")
    fname = f"{safe_pid}_{safe_cid}{ext}"
    out_path = out_dir / fname
    if out_path.exists() and out_path.stat().st_size > 0:
        try:
            return out_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            return str(out_path).replace("\\", "/")
    try:
        with httpx.Client(
            timeout=45,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://www.tiktok.com/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        ) as c:
            r = c.get(gif_url)
        if r.status_code != 200 or not r.content:
            return None
        if len(r.content) > MAX_TIKTOK_STICKER_BYTES:
            app.logger.warning("TikTok sticker too large (%s bytes), skip: %s", len(r.content), gif_url[:80])
            return None
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ct == "image/webp" and ext == ".bin":
            out_path = out_dir / f"{safe_pid}_{safe_cid}.webp"
        elif ct in ("image/jpeg", "image/jpg") and ext == ".bin":
            out_path = out_dir / f"{safe_pid}_{safe_cid}.jpg"
        elif ct == "image/png" and ext == ".bin":
            out_path = out_dir / f"{safe_pid}_{safe_cid}.png"
        elif ct == "image/gif" and ext == ".bin":
            out_path = out_dir / f"{safe_pid}_{safe_cid}.gif"
        out_path.write_bytes(r.content)
        return out_path.relative_to(PROJECT_ROOT).as_posix()
    except Exception as exc:
        app.logger.debug("TikTok sticker download failed: %s", exc)
        return None


def _download_media_asset(url: str, target_dir: Path, filename_base: str) -> str | None:
    if not url:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    ext = _guess_media_ext(url, ".jpg")
    out_path = target_dir / f"{filename_base}{ext}"
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.content:
            return None
        out_path.write_bytes(r.content)
        return str(out_path)
    except Exception:
        return None


# Instagram extension JSON roots (subfolders = different collection strata).
EXTENSION_SCRAPER_ROOT = PROJECT_ROOT / "data" / "raw" / "instagram_reels" / "extension_scraper"
# If request omits `stratum`, infer from subfolder name (lowercase key).
EXTENSION_SUBFOLDER_DEFAULT_STRATUM = {
    "neve": "cross_platform",
    "neve neve": "cross_platform",
    "scroll": "search_term",
}


def _safe_extension_subfolder(name: str | None) -> str | None:
    if not name or not str(name).strip():
        return None
    s = str(name).strip().replace("\\", "/").strip("/")
    if not s or ".." in s or s.startswith(("~", "/")):
        return None
    p = Path(s)
    if any(part == ".." for part in p.parts):
        return None
    return str(p)


@app.route("/api/instagram/refresh-extension", methods=["POST"])
def api_instagram_refresh_extension():
    """
    Import extension JSON exports from data/raw/instagram_reels/extension_scraper
    (top-level *.json) or from a subfolder (recursive *.json).

    Optional JSON body: subfolder, stratum, search_query, force.
    """
    from thesis_scraper.storage.database import get_connection, init_schema, insert_post, insert_comment
    from thesis_scraper.processors.standardizer import standardize_post, standardize_comment

    data = request.json or {}
    force = bool(data.get("force", False))
    sub_raw = _safe_extension_subfolder(data.get("subfolder"))
    folder = EXTENSION_SCRAPER_ROOT
    if not folder.exists():
        return jsonify({"error": f"Folder not found: {folder}"}), 404

    scan_root = folder / sub_raw if sub_raw else folder
    if not scan_root.exists():
        return jsonify({"error": f"Subfolder not found: {scan_root}"}), 404

    stratum_explicit = (data.get("stratum") or "").strip()
    if stratum_explicit:
        stratum = stratum_explicit
    elif sub_raw:
        stratum = EXTENSION_SUBFOLDER_DEFAULT_STRATUM.get(sub_raw.lower(), "search_term")
    else:
        stratum = "search_term"

    if stratum not in CANONICAL_STRATA:
        stratum = LEGACY_STRATA_TO_CANONICAL.get(stratum, "search_term")

    if stratum == "cross_platform":
        search_query = (data.get("search_query") or "").strip() or "wisewordsfromneve"
    else:
        search_query = (data.get("search_query") or "").strip() or "general"

    db_path = os.environ.get("TOSMOD_DB_PATH") or os.environ.get("THESIS_DB_PATH") or str(DEFAULT_DB)
    conn = get_connection(db_path)
    init_schema(conn)
    try:
        try:
            import yaml
            cfg_path = PROJECT_ROOT / "thesis_scraper" / "config" / "settings.yaml"
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        except Exception:
            cfg = {}
        salt = os.environ.get("ANONYMIZATION_SALT") or cfg.get("anonymization", {}).get("salt", "thesis_salt")

        if sub_raw:
            files = sorted(scan_root.rglob("*.json"))
        else:
            files = sorted(folder.glob("*.json"))
        if not files:
            return jsonify({"status": "ok", "files_processed": 0, "message": "No JSON files found", "scan_root": str(scan_root)})

        processed = 0
        skipped = 0
        imported_posts = 0
        imported_comments = 0
        warnings = []
        report = {"imported": [], "skipped": [], "errors": []}
        media_downloaded = 0
        media_failed = 0
        media_files: list[Path] = []
        media_root = PROJECT_ROOT / "data" / "raw" / "instagram_reels" / "extension_media"

        for fp in files:
            processed += 1
            try:
                payload = json.loads(fp.read_text(encoding="utf-8"))
            except Exception as exc:
                warnings.append(f"{fp.name}: invalid JSON ({exc})")
                skipped += 1
                report["errors"].append({"file": fp.name, "stage": "parse_json", "error": str(exc)})
                continue

            post_url = (payload.get("post_url") or "").strip()
            post_id = _extract_instagram_shortcode(post_url) or _extract_instagram_shortcode(fp.name)
            if not post_id:
                warnings.append(f"{fp.name}: could not extract post_id")
                skipped += 1
                report["skipped"].append({"file": fp.name, "stage": "extract_post_id", "reason": "could_not_extract_shortcode"})
                continue
            if _post_exists("instagram", post_id=post_id, url=post_url) and not force:
                skipped += 1
                report["skipped"].append({"file": fp.name, "stage": "precheck", "reason": "already_exists"})
                continue

            comments = payload.get("comments") or []
            if not isinstance(comments, list):
                comments = []
            if not comments:
                warnings.append(f"{fp.name}: no comments array")

            # Build post raw from extension payload (limited metadata available).
            post_raw = {
                "id": post_id,
                "caption": "",
                "createTime": payload.get("scraped_at"),
                "author": {"username": "extension_export"},
                "stats": {
                    "playCount": 0,
                    "diggCount": 0,
                    "shareCount": 0,
                    "commentCount": int(payload.get("total_comments") or len(comments) or 0),
                },
            }
            canonical_url = post_url or f"https://www.instagram.com/p/{post_id}/"
            meta = _fetch_instagram_page_meta(canonical_url)
            clean_caption = _normalize_instagram_caption(meta.get("caption") or meta.get("description") or "")
            if not meta.get("thumbnail_url"):
                thumb = _fetch_instagram_thumbnail_legacy_media(post_id) or _fetch_instagram_thumbnail_instaloader(post_id)
                if thumb:
                    meta["thumbnail_url"] = thumb
            if clean_caption:
                post_raw["caption"] = clean_caption
            if meta.get("thumbnail_url"):
                post_raw["thumbnail_url"] = meta.get("thumbnail_url")
            up = standardize_post(post_raw, canonical_url, "instagram", salt=salt)
            up.collection_stratum = stratum
            up.post_source = "instagram_extension"
            up.comments_source = "instagram_extension"
            insert_post(conn, up)
            conn.execute(
                """
                INSERT OR REPLACE INTO video_metadata
                (platform, post_id, title, thumbnail_url, channel_id, channel_title, duration_seconds, description, tags, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "instagram",
                    post_id,
                    clean_caption or meta.get("title") or "",
                    meta.get("thumbnail_url") or "",
                    "",
                    "instagram_extension",
                    None,
                    clean_caption or meta.get("description") or "",
                    json.dumps([], ensure_ascii=False),
                    _iso_now(),
                ),
            )
            if post_url:
                conn.execute(
                    "UPDATE posts SET search_query=? WHERE platform='instagram' AND post_id=?",
                    (search_query, post_id),
                )
            imported_posts += 1
            report["imported"].append({
                "file": fp.name,
                "post_id": post_id,
                "checks": {
                    "has_post_url": bool(post_url),
                    "has_shortcode": bool(post_id),
                    "has_comments_array": isinstance(payload.get("comments"), list),
                    "parsed_comment_count": len(comments),
                    "thumbnail_present": bool(meta.get("thumbnail_url")),
                    "caption_present": bool(clean_caption),
                },
            })

            # Quality checks
            if not post_url:
                warnings.append(f"{fp.name}: missing post_url")
            if not payload.get("total_comments"):
                warnings.append(f"{fp.name}: missing total_comments")

            for i, c in enumerate(comments):
                cid = str(c.get("id") or "").strip()
                if not cid:
                    warnings.append(f"{fp.name}: comment without id at index {i}")
                    continue
                media_type = str(c.get("Comment Media Type") or "").strip().lower()
                media_url = str(c.get("Comment Media URL") or "").strip()
                if not media_type:
                    media_str = str(c.get("Comment Media") or "").strip().lower()
                    if media_str == "gif":
                        media_type = "gif"
                    elif media_str == "image":
                        media_type = "image"
                if media_url.lower() == "not available":
                    media_url = ""
                has_gif = media_type == "gif" and bool(media_url)
                image_url = media_url if media_type == "image" else None
                local_media_path = None
                if image_url:
                    post_media_dir = media_root / post_id
                    local = _download_media_asset(image_url, post_media_dir, cid)
                    if local:
                        local_media_path = local
                        media_downloaded += 1
                        media_files.append(Path(local))
                    else:
                        media_failed += 1
                raw_comment = {
                    "id": cid,
                    "parent_comment_id": None if str(c.get("Parent Comment ID") or "").lower().startswith("not") else c.get("Parent Comment ID"),
                    "username": c.get("Author Username") or "",
                    "owner_id": c.get("Author ID") or "",
                    "text": c.get("Comment Text") or "",
                    "create_time": c.get("Comment Date"),
                    "digg_count": int(c.get("Comment Likes") or 0),
                    "reply_comment_total": int(c.get("Reply Count") or 0),
                    "has_gif": has_gif,
                    "gif_url": media_url if has_gif else None,
                    "gif_id": None,
                    "image_url": image_url,
                    "media_type": media_type or "none",
                    "media_url": media_url or None,
                    "local_media_path": local_media_path,
                    "thread_position": i,
                    "order_in_thread": 0,
                }
                # Skip duplicates unless force.
                exists_comment = conn.execute(
                    "SELECT comment_id FROM comments WHERE platform='instagram' AND post_id=? AND comment_id=?",
                    (post_id, cid),
                ).fetchone()
                if exists_comment and not force:
                    continue
                try:
                    uc = standardize_comment(
                        raw_comment,
                        "instagram",
                        depth=0,
                        thread_position=i,
                        thread_id=cid,
                        order_in_thread=0,
                        salt=salt,
                        scraped_at_iso=_iso_now(),
                    )
                    insert_comment(conn, uc, "instagram", post_id)
                    imported_comments += 1
                except Exception as exc:
                    warnings.append(f"{fp.name}: comment {cid} import failed ({exc})")
                    report["errors"].append({"file": fp.name, "stage": "comment_import", "comment_id": cid, "error": str(exc)})

        conn.commit()
        media_zip_path = None
        if media_files:
            bundles_dir = media_root / "_bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            zip_path = bundles_dir / f"instagram_extension_media_{stamp}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for fp in media_files:
                    if fp.exists():
                        try:
                            arcname = fp.relative_to(media_root)
                        except Exception:
                            arcname = fp.name
                        zf.write(fp, arcname=str(arcname))
            media_zip_path = str(zip_path)
        return jsonify({
            "status": "ok",
            "scan_root": str(scan_root),
            "subfolder": sub_raw or "",
            "stratum": stratum,
            "search_query": search_query,
            "files_processed": processed,
            "files_skipped": skipped,
            "posts_imported": imported_posts,
            "comments_imported": imported_comments,
            "warnings": warnings[:120],
            "report": report,
            "media": {
                "images_downloaded": media_downloaded,
                "images_failed": media_failed,
                "zip_path": media_zip_path,
            },
        })
    finally:
        conn.close()


# ── Backfill missing post metadata (IG/TikTok) ───────────────────────────────
@app.route("/api/backfill/post-metadata", methods=["POST"])
def api_backfill_post_metadata():
    """
    Backfill caption + thumbnail for existing Instagram/TikTok posts.
    Body: { "platforms": ["instagram","tiktok"] } (optional; default both)
    """
    data = request.json or {}
    req_platforms = data.get("platforms") or ["instagram", "tiktok"]
    platforms = [p for p in req_platforms if p in ("instagram", "tiktok")]
    if not platforms:
        platforms = ["instagram", "tiktok"]

    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        placeholders = ",".join(["?"] * len(platforms))
        rows = conn.execute(
            f"""
            SELECT p.platform, p.post_id, p.url, p.caption, vm.thumbnail_url
            FROM posts p
            LEFT JOIN video_metadata vm
              ON vm.platform=p.platform AND vm.post_id=p.post_id
            WHERE p.platform IN ({placeholders})
            ORDER BY p.platform, p.scraped_at DESC
            """,
            tuple(platforms),
        ).fetchall()
        updated = 0
        scanned = 0
        details = []
        reason_counts: dict[str, int] = {}
        skipped_samples: list[dict] = []
        for r in rows:
            scanned += 1
            platform = r["platform"]
            post_id = r["post_id"]
            url = r["url"] or ""
            caption = (r["caption"] or "").strip()
            thumb = (r["thumbnail_url"] or "").strip()

            new_caption = caption
            new_thumb = thumb
            channel_title = ""

            if platform == "instagram":
                # Fix encoded/boilerplate captions and recover missing thumbnails.
                fixed_caption = _normalize_instagram_caption(caption)
                if fixed_caption and fixed_caption != caption:
                    new_caption = fixed_caption
                ig_meta = _fetch_instagram_page_meta(url or f"https://www.instagram.com/p/{post_id}/")
                if not new_caption and ig_meta.get("caption"):
                    new_caption = _normalize_instagram_caption(ig_meta.get("caption"))
                if not new_thumb:
                    new_thumb = (
                        ig_meta.get("thumbnail_url")
                        or _fetch_instagram_thumbnail_legacy_media(post_id)
                        or _fetch_instagram_thumbnail_instaloader(post_id)
                        or ""
                    )
            elif platform == "tiktok":
                meta = _fetch_tiktok_oembed_meta(url)
                if not new_caption and meta.get("title"):
                    new_caption = meta["title"]
                if not new_thumb and meta.get("thumbnail_url"):
                    new_thumb = meta["thumbnail_url"]
                channel_title = meta.get("author_name", "")
                if meta.get("_error"):
                    reason = str(meta.get("_error"))
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1

            if (new_caption != caption) or (new_thumb != thumb):
                conn.execute(
                    "UPDATE posts SET caption=? WHERE platform=? AND post_id=?",
                    (new_caption, platform, post_id),
                )
                _upsert_video_metadata_row(
                    conn,
                    platform,
                    post_id,
                    title=new_caption,
                    thumbnail_url=new_thumb,
                    channel_title=channel_title,
                    description=new_caption,
                )
                updated += 1
                details.append({
                    "platform": platform,
                    "post_id": post_id,
                    "caption_filled": bool(new_caption and not caption),
                    "thumbnail_filled": bool(new_thumb and not thumb),
                })
            else:
                missing_parts = []
                if not caption and not new_caption:
                    missing_parts.append("caption_unresolved")
                if not thumb and not new_thumb:
                    missing_parts.append("thumbnail_unresolved")
                if missing_parts:
                    reason = "+".join(missing_parts)
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                    if len(skipped_samples) < 200:
                        skipped_samples.append(
                            {
                                "platform": platform,
                                "post_id": post_id,
                                "reason": reason,
                                "url_present": bool(url),
                                "thumb_present_before": bool(thumb),
                            }
                        )
        conn.commit()
        return jsonify({
            "status": "ok",
            "platforms": platforms,
            "scanned": scanned,
            "updated": updated,
            "details": details[:200],
            "skipped_reason_counts": reason_counts,
            "skipped_samples": skipped_samples,
        })
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Delete a single post (and all its related data) ────────────────────────────

@app.route("/api/delete/post", methods=["POST"])
def api_delete_post():
    data = request.json or {}
    platform = data.get("platform")
    post_id  = data.get("post_id")
    if not platform or not post_id:
        return jsonify({"error": "platform and post_id required"}), 400
    conn = get_db()
    if not conn:
        return jsonify({"error": "DB unavailable"}), 500
    try:
        conn.execute("DELETE FROM posts          WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM comments       WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM annotations    WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM video_metadata WHERE platform=? AND post_id=?", (platform, post_id))
        conn.execute("DELETE FROM transcripts    WHERE platform=? AND post_id=?", (platform, post_id))
        conn.commit()
        return jsonify({"deleted": True, "platform": platform, "post_id": post_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Clear database ─────────────────────────────────────────────────────────────

@app.route("/api/db/clear", methods=["POST"])
def api_db_clear():
    data = request.json or {}
    if data.get("confirm") != "yes":
        return jsonify({"error": 'Send {"confirm":"yes"} to confirm'}), 400

    table = data.get("table", "all")  # "posts" | "comments" | "all"
    conn = get_db()
    if not conn:
        return jsonify({"error": "Database not found"}), 404
    try:
        posts_deleted = comments_deleted = 0
        if table in ("posts", "all"):
            posts_deleted = conn.execute("DELETE FROM posts").rowcount
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM video_metadata")
            conn.execute("DELETE FROM transcripts")
            conn.commit()
        if table in ("comments", "all"):
            comments_deleted = conn.execute("DELETE FROM comments").rowcount
            conn.commit()
        return jsonify({"cleared": True, "posts_deleted": posts_deleted, "comments_deleted": comments_deleted})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Schema ─────────────────────────────────────────────────────────────────────

# ── Experiments (Phase 3) ────────────────────────────────────────────────────

_EXPERIMENT_PROCS: dict[str, subprocess.Popen] = {}


def _experiments_root() -> Path:
    return PROJECT_ROOT / "experiments"


def _experiments_results() -> Path:
    return _experiments_root() / "results"


def _spawn_experiment_script(rel_script: str, job_id: str, extra_args: list[str] | None = None) -> tuple[bool, str]:
    script = (_experiments_root() / Path(rel_script)).resolve()
    if not script.is_file():
        return False, f"Script not found: {script}"
    log_path = _experiments_results() / f"{job_id}.log"
    _experiments_results().mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    cmd = [sys.executable, str(script), "--job-id", job_id] + (extra_args or [])
    try:
        f = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        _EXPERIMENT_PROCS[job_id] = proc
    except Exception as exc:
        return False, str(exc)
    return True, job_id


def _spawn_root_script(rel_script: str, job_id: str, extra_args: list[str] | None = None) -> tuple[bool, str]:
    script = (PROJECT_ROOT / Path(rel_script)).resolve()
    if not script.is_file():
        return False, f"Script not found: {script}"
    log_path = _experiments_results() / f"{job_id}.log"
    _experiments_results().mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    cmd = [sys.executable, str(script)] + (extra_args or [])
    try:
        f = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        _EXPERIMENT_PROCS[job_id] = proc
    except Exception as exc:
        return False, str(exc)
    return True, job_id


@app.route("/api/experiments/env-check")
def api_experiments_env_check():
    mods = {}
    for name in ("bitsandbytes", "emoji", "statsmodels", "ollama", "transformers", "torch", "sklearn"):
        try:
            __import__(name if name != "sklearn" else "sklearn")
            mods[name] = True
        except ImportError:
            mods[name] = False
    return jsonify(
        {
            "modules": mods,
            "pip_hint": "pip install -r experiments/requirements.txt",
            "ollama_cli": "Install Ollama from https://ollama.com for optional CLI models (Tier 3).",
        }
    )


@app.route("/api/experiments/corpus-stats")
def api_experiments_corpus_stats():
    conn = get_db()
    if not conn:
        return jsonify({"error": "Database not found"}), 404
    try:
        total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        total_anno = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
        by_plat = {}
        for row in conn.execute(
            "SELECT platform, COUNT(*) FROM comments GROUP BY platform"
        ).fetchall():
            by_plat[row[0]] = row[1]
        by_label = {}
        for row in conn.execute(
            "SELECT label, COUNT(*) FROM annotations GROUP BY label"
        ).fetchall():
            by_label[row[0]] = row[1]
        gif_ct = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE COALESCE(has_gif,0)=1"
        ).fetchone()[0]
        ig_gif = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE platform='instagram' AND COALESCE(has_gif,0)=1"
        ).fetchone()[0]
        by_source = {}
        try:
            for row in conn.execute(
                "SELECT COALESCE(label_source,'human') AS s, COUNT(*) FROM annotations GROUP BY s"
            ).fetchall():
                by_source[row[0]] = row[1]
        except sqlite3.OperationalError:
            by_source = {"human": total_anno}
        return jsonify(
            {
                "total_comments": total_comments,
                "annotated": total_anno,
                "by_platform": by_plat,
                "by_label": by_label,
                "by_label_source": by_source,
                "gif_comments": gif_ct,
                "instagram_gif_comments": ig_gif,
            }
        )
    finally:
        conn.close()


@app.route("/api/experiments/results")
def api_experiments_results():
    path = _experiments_results() / "all_metrics.csv"
    if not path.is_file():
        return jsonify({"rows": [], "path": str(path)})
    rows = []
    experiment_id = (request.args.get("experiment_id") or "").strip()
    model_id = (request.args.get("model_id") or "").strip()
    recent_days = int(request.args.get("recent_days", "0") or 0)
    limit = max(1, min(int(request.args.get("limit", "200") or 200), 2000))
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if experiment_id and r.get("experiment_id") != experiment_id:
                continue
            if model_id and r.get("model_id") != model_id:
                continue
            if recent_days > 0:
                ts = (r.get("timestamp") or "").strip()
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
                    if age.days > recent_days:
                        continue
                except Exception:
                    continue
            rows.append(r)
    rows = sorted(rows, key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]
    return jsonify({"rows": rows, "path": str(path), "total_returned": len(rows)})


@app.route("/api/experiments/job-status")
def api_experiments_job_status():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    st_path = _experiments_results() / f"{job_id}.status.json"
    log_path = _experiments_results() / f"{job_id}.log"
    status = {}
    if st_path.is_file():
        try:
            status = json.loads(st_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {"error": "invalid status json"}
    log_tail = ""
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-80:])
    proc = _EXPERIMENT_PROCS.get(job_id)
    running = proc is not None and proc.poll() is None
    return jsonify({"job_id": job_id, "status": status, "log_tail": log_tail, "process_running": running})


@app.route("/api/annotate/ollama/youtube", methods=["POST"])
def api_annotate_ollama_youtube():
    """Background Ollama labelling for unannotated YouTube only (silver: label_source=llm, split=train).

    Starts ``experiments/01_annotation/llm_annotation_ollama.py``. Requires local Ollama.
    Poll status via ``/api/experiments/job-status?job_id=…`` (same as Experiments tab).
    """
    data = request.json or {}
    limit = max(1, min(int(data.get("limit", 100)), 5000))
    model = (data.get("model") or "mistral").strip() or "mistral"
    job_id = (data.get("job_id") or "").strip() or f"anno-ollama-yt-{uuid.uuid4().hex[:12]}"
    extra = ["--platform", "youtube", "--limit", str(limit), "--model", model]
    ok, msg = _spawn_experiment_script("01_annotation/llm_annotation_ollama.py", job_id, extra)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/experiments/annotate/run", methods=["POST"])
def api_experiments_annotate_run():
    data = request.json or {}
    limit = max(1, min(int(data.get("limit", 200)), 5000))
    model = (data.get("model") or "").strip()
    extra = ["--limit", str(limit)]
    if model:
        extra += ["--model", model]
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_experiment_script(Path("01_annotation") / "llm_annotation.py", job_id, extra)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/experiments/preprocess/splits", methods=["POST"])
def api_experiments_preprocess_splits():
    job_id = (request.json or {}).get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_experiment_script(
        Path("02_text_classification") / "preprocess.py", job_id, ["--assign-splits"]
    )
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/iaa/sample-double-pool", methods=["POST"])
def api_iaa_sample_double_pool():
    data = request.json or {}
    frac = float(data.get("fraction", 0.08))
    seed = int(data.get("seed", 42))
    reset = bool(data.get("reset", False))
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    extra = ["--fraction", str(frac), "--seed", str(seed)]
    if reset:
        extra.append("--reset")
    ok, msg = _spawn_root_script(Path("scripts") / "sample_double_annotation_pool.py", job_id, extra)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/iaa/export-template", methods=["POST"])
def api_iaa_export_template():
    data = request.json or {}
    out = (data.get("out") or "experiments/results/iaa_second_annotation_template.csv").strip()
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_root_script(
        Path("scripts") / "export_iaa_second_annotation_template.py",
        job_id,
        ["--out", out],
    )
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True, "out": out})


@app.route("/api/iaa/run-human-kappa", methods=["POST"])
def api_iaa_run_human_kappa():
    data = request.json or {}
    reviewer = (data.get("reviewer") or "r2").strip() or "r2"
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_experiment_script(
        Path("01_annotation") / "human_iaa.py",
        job_id,
        ["--reviewer", reviewer],
    )
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True, "reviewer": reviewer})


@app.route("/api/experiments/text/run", methods=["POST"])
def api_experiments_text_run():
    data = request.json or {}
    kind = (data.get("kind") or "baseline").lower()
    variant = (data.get("variant") or "T").upper()
    if variant not in ("T", "T+E", "T+ED"):
        variant = "T"
    model = (data.get("model") or "lr").lower()
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    if kind == "baseline":
        script = Path("02_text_classification") / "baselines.py"
        extra = ["--variant", variant, "--model", model if model in ("lr", "svm") else "lr"]
    elif kind == "transformer":
        script = Path("02_text_classification") / "transformer_finetune.py"
        m = model if model in ("roberta", "hatebert", "toxicbert") else "roberta"
        extra = ["--variant", variant, "--model", m]
        if data.get("quick"):
            extra.append("--quick")
    elif kind == "few_shot":
        script = Path("02_text_classification") / "few_shot_llm.py"
        extra = ["--variant", variant]
        mt = (data.get("llm_model") or "").strip()
        if mt:
            extra += ["--model", mt]
        if data.get("max_test"):
            extra += ["--max-test", str(int(data["max_test"]))]
    else:
        return jsonify({"error": "kind must be baseline|transformer|few_shot"}), 400
    ok, msg = _spawn_experiment_script(script, job_id, extra)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True, "kind": kind})


@app.route("/api/experiments/multimodal/run", methods=["POST"])
def api_experiments_multimodal_run():
    data = request.json or {}
    phase = (data.get("phase") or "describe").lower()
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    if phase == "describe":
        lim = max(1, min(int(data.get("limit", 20)), 200))
        ok, msg = _spawn_experiment_script(
            Path("03_multimodal") / "vlm_describe.py", job_id, ["--limit", str(lim)]
        )
    elif phase == "evaluate":
        ok, msg = _spawn_experiment_script(Path("03_multimodal") / "multimodal_evaluate.py", job_id, [])
    else:
        return jsonify({"error": "phase must be describe|evaluate"}), 400
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True, "phase": phase})


@app.route("/api/experiments/custom/run", methods=["POST"])
def api_experiments_custom_run():
    data = request.json or {}
    mode = (data.get("mode") or "mlm").lower()
    job_id = data.get("job_id") or uuid.uuid4().hex[:12]
    if mode == "mlm":
        extra = ["--quick"] if data.get("quick") else []
        ok, msg = _spawn_experiment_script(Path("04_custom_model") / "continued_pretrain.py", job_id, extra)
    elif mode == "finetune":
        extra = []
        if data.get("variant"):
            extra += ["--variant", str(data["variant"])]
        ok, msg = _spawn_experiment_script(Path("04_custom_model") / "multitask_finetune.py", job_id, extra)
    else:
        return jsonify({"error": "mode must be mlm|finetune"}), 400
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/experiments/bias/run", methods=["POST"])
def api_experiments_bias_run():
    job_id = (request.json or {}).get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_experiment_script(Path("05_bias_audit") / "bias_probe.py", job_id, [])
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/experiments/agreement/run", methods=["POST"])
def api_experiments_agreement_run():
    job_id = (request.json or {}).get("job_id") or uuid.uuid4().hex[:12]
    ok, msg = _spawn_experiment_script(Path("01_annotation") / "agreement_metrics.py", job_id, [])
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"job_id": job_id, "started": True})


@app.route("/api/experiments/export-annotations")
def api_experiments_export_annotations():
    """Export annotations joined with comments to CSV (thesis deliverable path)."""
    conn = get_db()
    if not conn:
        return jsonify({"error": "Database not found"}), 404
    out_dir = PROJECT_ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "annotations_final.csv"
    try:
        rows = conn.execute(
            """
            SELECT c.platform, c.post_id, c.comment_id, c.text,
                   a.label, a.severity, a.modality, a.harmful, a.uncertain, a.gif_context, a.annotated_at,
                   COALESCE(a.label_source,'human') AS label_source, a.split,
                   p.collection_stratum, p.search_query
            FROM annotations a
            JOIN comments c ON c.platform=a.platform AND c.post_id=a.post_id AND c.comment_id=a.comment_id
            JOIN posts p ON p.platform=a.platform AND p.post_id=a.post_id
            """
        ).fetchall()
        fieldnames = [
            "platform", "post_id", "comment_id", "text", "label", "severity", "modality", "harmful",
            "uncertain", "gif_context", "annotated_at", "label_source", "split",
            "collection_stratum", "search_query",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=fieldnames)
            wr.writeheader()
            for r in rows:
                wr.writerow({k: r[k] for k in fieldnames})
        return jsonify({"ok": True, "path": str(out_path), "rows": len(rows)})
    finally:
        conn.close()


@app.route("/api/schema")
def api_schema():
    return jsonify({
        "posts": {
            "primary_key": ["platform", "post_id"],
            "columns": [
                "platform", "post_id", "url", "author_id", "caption", "posted_at",
                "views", "likes", "shares", "comments_count", "scraped_at",
                "collection_stratum", "search_query", "post_source", "comments_source",
            ],
        },
        "comments": {
            "primary_key": ["platform", "post_id", "comment_id"],
            "columns": [
                "platform", "post_id", "comment_id", "parent_comment_id", "author_id", "text",
                "posted_at", "likes", "reply_count", "depth", "thread_position",
                "thread_id", "order_in_thread", "has_gif", "gif_url", "gif_id", "gif_local_path",
                "platform_raw_timestamp", "scraped_at",
            ],
        },
        "annotations": {
            "primary_key": ["platform", "post_id", "comment_id"],
            "columns": [
                "platform", "post_id", "comment_id", "label", "severity",
                "modality", "harmful", "uncertain", "gif_context", "annotated_at",
                "label_source", "split",
            ],
        },
    })


@app.route("/docs/legal")
def serve_legal():
    legal = PROJECT_ROOT / "tosmod" / "connectors" / "opt_in" / "LEGAL.md"
    if legal.exists():
        return Response(legal.read_text(encoding="utf-8"), mimetype="text/markdown")
    return Response("Not found", status=404)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=True)
