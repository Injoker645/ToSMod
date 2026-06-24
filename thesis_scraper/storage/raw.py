"""
Save raw HTML/JSON by platform and date.
Dual storage for re-parsing after schema or platform changes.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from thesis_scraper.storage.models import RawCommentPayload, RawPostPayload


def _raw_dir(base_dir: str, platform: str, date_str: Optional[str] = None) -> Path:
    if date_str is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    return Path(base_dir) / platform / date_str


def save_raw_post(base_dir: str, payload: RawPostPayload, date_str: Optional[str] = None) -> Path:
    """Save raw post HTML/JSON to base_dir/platform/date/post_{post_id}.json."""
    d = _raw_dir(base_dir, payload.platform, date_str)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"post_{payload.post_id}.json"
    data = {
        "platform": payload.platform,
        "post_id": payload.post_id,
        "url": payload.url,
        "scraped_at": payload.scraped_at,
        "raw_html": payload.raw_html,
        "raw_json": payload.raw_json,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return out


def save_raw_comments_batch(
    base_dir: str,
    platform: str,
    post_id: str,
    raw_json: dict,
    source: str = "comment/list",
    date_str: Optional[str] = None,
) -> Path:
    """Save a single comment batch (API response) to raw storage."""
    d = _raw_dir(base_dir, platform, date_str)
    d.mkdir(parents=True, exist_ok=True)
    scraped_at = datetime.utcnow().isoformat() + "Z"
    # Append to a single file per post or use batch index; use batch index for multiple pages
    out = d / f"comments_{post_id}.json"
    # If file exists, load and append; else create list
    if out.exists():
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("batches"), list):
            data = {"post_id": post_id, "platform": platform, "scraped_at": scraped_at, "batches": [data]}
        data["batches"].append({"source": source, "scraped_at": scraped_at, "data": raw_json})
    else:
        data = {
            "post_id": post_id,
            "platform": platform,
            "scraped_at": scraped_at,
            "batches": [{"source": source, "scraped_at": scraped_at, "data": raw_json}],
        }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return out


def load_raw_post(base_dir: str, platform: str, post_id: str, date_str: Optional[str] = None) -> Optional[dict]:
    """Load raw post JSON if present."""
    d = _raw_dir(base_dir, platform, date_str)
    out = d / f"post_{post_id}.json"
    if not out.exists():
        return None
    with open(out, "r", encoding="utf-8") as f:
        return json.load(f)
