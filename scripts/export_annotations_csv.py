"""Export annotations_final.csv (same columns as dashboard API)."""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thesis_scraper.storage.database import init_schema

from experiments.config import get_db_connection


def main() -> None:
    conn = get_db_connection()
    init_schema(conn)
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "annotations_final.csv"
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
    conn.close()
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
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
