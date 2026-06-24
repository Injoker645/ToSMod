"""Map external CSV/JSON/JSONL into ToSMod SQLite schema."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tosmod.config.loader import ConfigLoader, get_config


@dataclass
class ImportResult:
    rows_read: int = 0
    comments_upserted: int = 0
    posts_upserted: int = 0
    annotations_upserted: int = 0
    errors: list[str] = field(default_factory=list)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _json_path_get(obj: Any, path: str) -> Any:
    """Minimal JSONPath: $.a.b or $.items[*].text"""
    if not path or not path.startswith("$."):
        return None
    parts = path[2:].split(".")
    cur = obj
    for part in parts:
        if part.endswith("[*]"):
            key = part[:-3]
            if key:
                cur = cur.get(key) if isinstance(cur, dict) else None
            if not isinstance(cur, list):
                return None
            return cur
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class ImportEngine:
    def __init__(self, config: ConfigLoader | None = None) -> None:
        self.config = config or get_config()
        self.valid_labels = set(self.config.label_names())

    def load_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return profile

    def _apply_transform(self, value: str, transform: str, row: dict[str, Any]) -> str:
        if transform == "strip_html":
            return _strip_html(value)
        if transform.startswith("anonymize_author:"):
            col = transform.split(":", 1)[1]
            raw = row.get(col, "")
            return f"anon_{hash(str(raw)) & 0xFFFFFFFF:08x}"
        return value

    def _map_row(self, raw: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        fmap = profile.get("field_map", {})
        platform_default = profile.get("platform_default", "custom")
        out: dict[str, Any] = {}
        for canonical, source in fmap.items():
            if isinstance(source, str) and source in raw:
                out[canonical] = raw.get(source)
        if "platform" not in out or not out["platform"]:
            out["platform"] = platform_default
        if "post_id" not in out or not out["post_id"]:
            out["post_id"] = f"import_{uuid.uuid4().hex[:12]}"
        if "comment_id" not in out or not out["comment_id"]:
            out["comment_id"] = f"c_{uuid.uuid4().hex[:12]}"
        transforms = profile.get("transforms", [])
        for t in transforms:
            if isinstance(t, str) and t == "strip_html" and out.get("text"):
                out["text"] = _strip_html(str(out["text"]))
            elif isinstance(t, str) and t.startswith("anonymize_author:"):
                pass
        return out

    def iter_rows(self, file_path: Path, profile: dict[str, Any]) -> Iterator[dict[str, Any]]:
        fmt = profile.get("format", "csv").lower()
        if fmt == "csv":
            with file_path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    yield self._map_row(row, profile)
        elif fmt in ("json", "jsonl"):
            if fmt == "jsonl":
                with file_path.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        raw = json.loads(line)
                        if isinstance(raw, dict):
                            yield self._map_row(raw, profile)
            else:
                with file_path.open(encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for raw in data:
                        if isinstance(raw, dict):
                            yield self._map_row(raw, profile)
                elif isinstance(data, dict):
                    yield self._map_row(data, profile)

    def preview(self, file_path: Path, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i, row in enumerate(self.iter_rows(file_path, profile)):
            rows.append(row)
            if i + 1 >= limit:
                break
        return rows

    def validate_row(self, row: dict[str, Any]) -> list[str]:
        errs: list[str] = []
        if not (row.get("text") or "").strip():
            errs.append("missing text")
        label = row.get("label")
        if label and str(label).upper() not in self.valid_labels:
            errs.append(f"invalid label: {label}")
        return errs

    def import_file(
        self,
        conn: sqlite3.Connection,
        file_path: Path,
        profile: dict[str, Any],
        limit: int | None = None,
    ) -> ImportResult:
        result = ImportResult()
        now = datetime.now(timezone.utc).isoformat()
        harmful = self.config.harmful_labels()
        for row in self.iter_rows(file_path, profile):
            result.rows_read += 1
            if limit and result.rows_read > limit:
                break
            verrs = self.validate_row(row)
            if verrs:
                result.errors.append(f"row {result.rows_read}: {', '.join(verrs)}")
                continue
            platform = str(row["platform"]).lower()
            post_id = str(row["post_id"])
            comment_id = str(row["comment_id"])
            text = str(row.get("text", "")).strip()
            post_url = row.get("url") or f"https://tosmod.local/{platform}/{post_id}"
            conn.execute(
                """
                INSERT INTO posts (platform, post_id, url, author_id, caption, scraped_at, collection_stratum, search_query)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, post_id) DO UPDATE SET
                  caption=COALESCE(excluded.caption, posts.caption)
                """,
                (
                    platform,
                    post_id,
                    post_url,
                    row.get("channel_name") or "imported",
                    row.get("post_title") or row.get("title") or row.get("caption"),
                    now,
                    row.get("collection_stratum") or "import",
                    row.get("search_query"),
                ),
            )
            result.posts_upserted += 1
            conn.execute(
                """
                INSERT INTO comments (platform, post_id, comment_id, text, author_id, posted_at, parent_comment_id, has_gif, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, post_id, comment_id) DO UPDATE SET text=excluded.text
                """,
                (
                    platform,
                    post_id,
                    comment_id,
                    text,
                    row.get("author_id") or "imported",
                    row.get("posted_at") or now,
                    row.get("parent_comment_id"),
                    1 if row.get("has_gif") else 0,
                    json.dumps(row.get("raw_json") or {}),
                ),
            )
            result.comments_upserted += 1
            label = row.get("label")
            if label:
                label_u = str(label).upper()
                severity = row.get("severity")
                modality = row.get("modality") or "text"
                harmful_flag = 0 if label_u not in harmful else 1
                conn.execute(
                    """
                    INSERT INTO annotations (platform, post_id, comment_id, label, severity, modality, harmful, uncertain, gif_context, annotated_at, label_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import')
                    ON CONFLICT(platform, post_id, comment_id) DO UPDATE SET
                      label=excluded.label, severity=excluded.severity, modality=excluded.modality,
                      harmful=excluded.harmful
                    """,
                    (
                        platform,
                        post_id,
                        comment_id,
                        label_u,
                        severity,
                        modality,
                        harmful_flag,
                        1 if row.get("uncertain") else 0,
                        row.get("gif_context"),
                        now,
                    ),
                )
                result.annotations_upserted += 1
        conn.commit()
        return result
