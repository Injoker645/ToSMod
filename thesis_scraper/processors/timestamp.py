"""
Normalize timestamps to ISO8601; handle relative times and platform raw values.
"""
import re
from datetime import datetime, timezone
from typing import Optional, Tuple


def unix_to_iso8601(ts: Optional[int]) -> Optional[str]:
    """Convert Unix timestamp (seconds) to ISO8601 string (UTC)."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OSError):
        return None


def iso8601_to_unix(iso: Optional[str]) -> Optional[int]:
    """Parse ISO8601 to Unix timestamp."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


# Relative time patterns: "2h ago", "3d ago", "1w ago", "2 hours ago"
RELATIVE_PATTERNS = [
    (re.compile(r"(\d+)\s*(?:hour|hr|h)s?\s*ago", re.I), 3600),
    (re.compile(r"(\d+)\s*(?:minute|min|m)s?\s*ago", re.I), 60),
    (re.compile(r"(\d+)\s*(?:day|d)s?\s*ago", re.I), 86400),
    (re.compile(r"(\d+)\s*(?:week|w)s?\s*ago", re.I), 604800),
    (re.compile(r"(\d+)\s*(?:month|mo)s?\s*ago", re.I), 2592000),
    (re.compile(r"(\d+)\s*(?:year|y)s?\s*ago", re.I), 31536000),
    (re.compile(r"(\d+)\s*h\s*ago", re.I), 3600),
    (re.compile(r"(\d+)\s*d\s*ago", re.I), 86400),
    (re.compile(r"(\d+)\s*w\s*ago", re.I), 604800),
]


def parse_relative_time(relative_str: str, reference_ts: Optional[float] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse relative time string (e.g. "2h ago") into (iso8601, platform_raw_timestamp).
    reference_ts: Unix timestamp when the page was scraped (default: now).
    Returns (iso8601_str, platform_raw_timestamp_str for reproducibility).
    """
    if not relative_str or not relative_str.strip():
        return None, relative_str
    ref = reference_ts if reference_ts is not None else datetime.now(tz=timezone.utc).timestamp()
    for pattern, seconds_per_unit in RELATIVE_PATTERNS:
        m = pattern.search(relative_str.strip())
        if m:
            n = int(m.group(1))
            delta_seconds = n * seconds_per_unit
            ts = ref - delta_seconds
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            return iso, relative_str.strip()
    return None, relative_str.strip()


def normalize_timestamp(
    value,
    platform: str,
    scraped_at_iso: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize platform timestamp to (iso8601, platform_raw_timestamp).
    value: int (Unix), str (ISO or relative like "2h ago"), or None.
    """
    raw_str = None
    if value is None:
        return None, None
    if isinstance(value, int):
        iso = unix_to_iso8601(value)
        raw_str = str(value)
        return iso, raw_str
    if isinstance(value, str):
        if value.isdigit():
            return unix_to_iso8601(int(value)), value
        if "T" in value or "-" in value:
            return value.replace("+00:00", "Z"), value
        ref_ts = iso8601_to_unix(scraped_at_iso) if scraped_at_iso else None
        return parse_relative_time(value, ref_ts)
    return None, str(value)
