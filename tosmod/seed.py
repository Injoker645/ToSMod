"""Seed local SQLite DB from examples/data for annotation smoke tests."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from thesis_scraper.storage.database import init_schema
from tosmod.config.loader import get_config
from tosmod.import_.engine import ImportEngine
from tosmod.paths import PROJECT_ROOT


def seed_demo_db(db_path: Path | None = None) -> None:
    cfg = get_config()
    db_path = db_path or cfg.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()

    posts_path = PROJECT_ROOT / "examples" / "data" / "demo_posts.json"
    if posts_path.exists():
        posts = json.loads(posts_path.read_text(encoding="utf-8"))
        for p in posts:
            conn.execute(
                """
                INSERT INTO posts (platform, post_id, url, author_id, caption, scraped_at, collection_stratum, search_query)
                VALUES (?, ?, ?, ?, ?, ?, 'demo', 'synthetic_demo')
                ON CONFLICT(platform, post_id) DO UPDATE SET caption=excluded.caption
                """,
                (
                    p["platform"],
                    p["post_id"],
                    p.get("url") or f"https://example.com/{p['platform']}/{p['post_id']}",
                    p.get("channel_name") or "demo",
                    p.get("title"),
                    now,
                ),
            )
            ctx = p.get("annotation_context")
            if ctx:
                conn.execute(
                    """
                    INSERT INTO video_metadata (platform, post_id, title, description, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(platform, post_id) DO UPDATE SET description=excluded.description
                    """,
                    (p["platform"], p["post_id"], p.get("title"), ctx, now),
                )
        conn.commit()

    csv_path = PROJECT_ROOT / "examples" / "data" / "demo_comments.csv"
    profile_path = cfg.config_dir / "import_profiles" / "tosmod_canonical.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    result = ImportEngine(cfg).import_file(conn, csv_path, profile)
    print(f"Seeded {db_path}")
    print(f"  comments: {result.comments_upserted}, annotations: {result.annotations_upserted}")
    if result.errors:
        print("  errors:", result.errors[:5])
    conn.close()


def main() -> None:
    override = os.environ.get("TOSMOD_DB_PATH")
    seed_demo_db(Path(override) if override else None)


if __name__ == "__main__":
    main()
