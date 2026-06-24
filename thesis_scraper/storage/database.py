"""
Insert unified records into SQLite (or PostgreSQL).
Schema: posts table, comments table.
"""
import json
import sqlite3
from pathlib import Path
from typing import Any, List, Optional

from thesis_scraper.storage.models import UnifiedComment, UnifiedPost, comment_to_dict, post_to_dict


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_schema(conn: sqlite3.Connection) -> None:
    """Create posts and comments tables if not exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            url TEXT NOT NULL,
            author_id TEXT NOT NULL,
            caption TEXT,
            posted_at TEXT,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            comments_count INTEGER DEFAULT 0,
            scraped_at TEXT NOT NULL,
            collection_stratum TEXT,
            search_query TEXT,
            raw_json TEXT,
            post_source TEXT,
            comments_source TEXT,
            PRIMARY KEY (platform, post_id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            parent_comment_id TEXT,
            author_id TEXT NOT NULL,
            text TEXT NOT NULL,
            posted_at TEXT,
            likes INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            depth INTEGER DEFAULT 0,
            thread_position INTEGER DEFAULT 0,
            thread_id TEXT,
            order_in_thread INTEGER DEFAULT 0,
            platform_raw_timestamp TEXT,
            raw_json TEXT,
            scraped_at TEXT,
            has_gif INTEGER DEFAULT 0,
            gif_url TEXT,
            gif_id TEXT,
            gif_local_path TEXT,
            PRIMARY KEY (platform, post_id, comment_id)
        );
        CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(platform, post_id);
        CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(platform, post_id, parent_comment_id);
        CREATE INDEX IF NOT EXISTS idx_comments_thread ON comments(platform, post_id, thread_id);

        -- Rich metadata fetched separately (title, channel, duration, description, tags, thumbnail)
        CREATE TABLE IF NOT EXISTS video_metadata (
            platform        TEXT NOT NULL,
            post_id         TEXT NOT NULL,
            title           TEXT,
            thumbnail_url   TEXT,
            channel_id      TEXT,
            channel_title   TEXT,
            duration_seconds INTEGER,
            description     TEXT,
            tags            TEXT,          -- JSON array
            fetched_at      TEXT NOT NULL,
            PRIMARY KEY (platform, post_id)
        );

        -- Auto-generated or manual transcripts + extractive summary
        CREATE TABLE IF NOT EXISTS transcripts (
            platform        TEXT NOT NULL,
            post_id         TEXT NOT NULL,
            language        TEXT,
            language_code   TEXT,
            is_generated    INTEGER DEFAULT 1,
            full_text       TEXT,          -- concatenated transcript
            summary         TEXT,          -- extractive summary (~150 words)
            word_count      INTEGER,
            fetched_at      TEXT NOT NULL,
            PRIMARY KEY (platform, post_id)
        );

        CREATE TABLE IF NOT EXISTS annotations (
            platform      TEXT NOT NULL,
            post_id       TEXT NOT NULL,
            comment_id    TEXT NOT NULL,
            label         TEXT NOT NULL,
            severity      INTEGER,
            modality      TEXT NOT NULL,
            harmful       INTEGER NOT NULL,
            uncertain     INTEGER DEFAULT 0,
            gif_context   TEXT,
            annotated_at  TEXT NOT NULL,
            label_source  TEXT DEFAULT 'human',
            split         TEXT,
            PRIMARY KEY (platform, post_id, comment_id)
        );

        CREATE INDEX IF NOT EXISTS idx_annotations_label ON annotations(label);
        CREATE INDEX IF NOT EXISTS idx_annotations_platform ON annotations(platform);
        CREATE INDEX IF NOT EXISTS idx_annotations_source_split ON annotations(label_source, split);

        CREATE TABLE IF NOT EXISTS llm_annotation_debug (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            prompt_mode TEXT NOT NULL,
            model_name TEXT,
            raw_output TEXT,
            parse_ok INTEGER NOT NULL,
            parse_error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_llm_debug_key ON llm_annotation_debug(platform, post_id, comment_id);
        CREATE INDEX IF NOT EXISTS idx_llm_debug_job ON llm_annotation_debug(job_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS iaa_double_annotation_pool (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            sampled_at TEXT NOT NULL,
            sampler TEXT NOT NULL DEFAULT 'system',
            PRIMARY KEY (platform, post_id, comment_id)
        );
        CREATE TABLE IF NOT EXISTS iaa_second_annotations (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            label TEXT NOT NULL,
            severity INTEGER,
            modality TEXT,
            uncertain INTEGER DEFAULT 0,
            annotated_at TEXT NOT NULL,
            PRIMARY KEY (platform, post_id, comment_id, reviewer)
        );

        CREATE TABLE IF NOT EXISTS search_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            platform        TEXT NOT NULL,
            query_text      TEXT,
            filters_json    TEXT,
            result_ids_json TEXT,
            result_count    INTEGER DEFAULT 0,
            run_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_search_runs_platform_runat ON search_runs(platform, run_at DESC);

        CREATE TABLE IF NOT EXISTS ingestion_failures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            platform        TEXT NOT NULL,
            post_id         TEXT,
            url             TEXT,
            reason_code     TEXT NOT NULL,
            reason_detail   TEXT,
            source_context  TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ing_fail_platform_post ON ingestion_failures(platform, post_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ing_fail_reason ON ingestion_failures(reason_code, created_at DESC);
    """)
    # Migrate existing DBs: add new columns if missing
    for col in ("post_source", "comments_source", "collection_stratum", "search_query"):
        try:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
    for col, typ in (
        ("thread_id", "TEXT"),
        ("order_in_thread", "INTEGER DEFAULT 0"),
        ("has_gif", "INTEGER DEFAULT 0"),
        ("gif_url", "TEXT"),
        ("gif_id", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE comments ADD COLUMN {col} {typ}")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
    try:
        conn.execute("ALTER TABLE video_metadata ADD COLUMN annotation_context TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    try:
        conn.execute("ALTER TABLE annotations ADD COLUMN gif_context TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    try:
        conn.execute("ALTER TABLE comments ADD COLUMN gif_local_path TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    for col, typ in (
        ("label_source", "TEXT"),
        ("split", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE annotations ADD COLUMN {col} {typ}")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()

    # Derived context for experiments (thumbnail OCR/caption; per-comment engagement ratios).
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS post_visual_context (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            thumbnail_source_url TEXT,
            thumbnail_local_path TEXT,
            thumbnail_ocr_text TEXT,
            thumbnail_caption TEXT,
            models_used TEXT,
            wall_ms INTEGER,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (platform, post_id)
        );
        CREATE TABLE IF NOT EXISTS comment_engagement_features (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            post_views INTEGER,
            post_likes INTEGER,
            post_comments_count INTEGER,
            comment_likes INTEGER,
            section_median_comment_likes REAL,
            like_ratio_to_post_likes REAL,
            like_ratio_to_median_comment_likes REAL,
            rank_likes_in_post INTEGER,
            depth INTEGER,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (platform, post_id, comment_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cef_post ON comment_engagement_features(platform, post_id);
        CREATE TABLE IF NOT EXISTS thumbnail_cache_status (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            source_url TEXT,
            local_path TEXT,
            last_fetch_status TEXT,
            last_fetch_error TEXT,
            http_status INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            cached_at TEXT,
            PRIMARY KEY (platform, post_id)
        );
        """
    )
    conn.commit()
    # Backward-compatible migration for existing DBs.
    try:
        conn.execute("ALTER TABLE post_visual_context ADD COLUMN thumbnail_local_path TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
    for col, typ in (
        ("thumbnail_ocr_cleaned", "TEXT"),
        ("thumbnail_ocr_cleaned_at", "TEXT"),
        ("thumbnail_ocr_cleaned_model", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE post_visual_context ADD COLUMN {col} {typ}")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
    # Cache/status migration helpers.
    for col, typ in (
        ("local_path", "TEXT"),
        ("last_fetch_status", "TEXT"),
        ("last_fetch_error", "TEXT"),
        ("http_status", "INTEGER"),
        ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_attempt_at", "TEXT"),
        ("cached_at", "TEXT"),
        ("source_url", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE thumbnail_cache_status ADD COLUMN {col} {typ}")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()


def insert_post(conn: sqlite3.Connection, post: UnifiedPost) -> None:
    """Insert or replace a single post."""
    d = post_to_dict(post)
    raw_json = json.dumps(d.pop("raw", None)) if d.get("raw") else None
    conn.execute(
        """
        INSERT OR REPLACE INTO posts (
            platform, post_id, url, author_id, caption, posted_at,
            views, likes, shares, comments_count, scraped_at, raw_json,
            post_source, comments_source, collection_stratum
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            d["platform"],
            d["post_id"],
            d["url"],
            d["author_id"],
            d["caption"],
            d["posted_at"],
            d["metrics"]["views"],
            d["metrics"]["likes"],
            d["metrics"]["shares"],
            d["metrics"]["comments_count"],
            d["scraped_at"],
            raw_json,
            d.get("post_source"),
            d.get("comments_source"),
            d.get("collection_stratum"),
        ),
    )
    conn.commit()


def insert_comment(conn: sqlite3.Connection, comment: UnifiedComment, platform: str, post_id: str) -> None:
    """Insert or replace a single comment."""
    d = comment_to_dict(comment)
    raw_json = json.dumps(d.pop("raw", None)) if d.get("raw") else None
    scraped_at = d.get("scraped_at")
    conn.execute(
        """
        INSERT OR REPLACE INTO comments (
            platform, post_id, comment_id, parent_comment_id, author_id, text,
            posted_at, likes, reply_count, depth, thread_position,
            thread_id, order_in_thread,
            platform_raw_timestamp, raw_json, scraped_at,
            has_gif, gif_url, gif_id, gif_local_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            platform,
            post_id,
            d["comment_id"],
            d["parent_comment_id"],
            d["author_id"],
            d["text"],
            d["posted_at"],
            d["likes"],
            d["reply_count"],
            d["depth"],
            d["thread_position"],
            d.get("thread_id"),
            d.get("order_in_thread", 0),
            d.get("platform_raw_timestamp"),
            raw_json,
            scraped_at,
            1 if d.get("has_gif") else 0,
            d.get("gif_url"),
            d.get("gif_id"),
            d.get("gif_local_path"),
        ),
    )
    conn.commit()


def insert_posts(conn: sqlite3.Connection, posts: List[UnifiedPost]) -> None:
    for p in posts:
        insert_post(conn, p)


def insert_comments(
    conn: sqlite3.Connection,
    comments: List[UnifiedComment],
    platform: str,
    post_id: str,
) -> None:
    for c in comments:
        insert_comment(conn, c, platform, post_id)
