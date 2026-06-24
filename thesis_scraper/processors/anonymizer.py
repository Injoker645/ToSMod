"""
Anonymization: stable hash of author IDs per platform.
SHA-256(platform + raw_id + salt) for consistent mapping across runs.
"""
import hashlib
from typing import Optional


def hash_author_id(platform: str, raw_id: str, salt: str) -> str:
    """
    Produce stable author_id for storage.
    Use same salt for the whole thesis so same user maps to same hash.
    """
    if not raw_id:
        return ""
    payload = f"{platform}:{raw_id}:{salt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def anonymize_author(platform: str, raw_identifier: str, salt: str) -> str:
    """
    raw_identifier: username, user id, channel id, etc.
    Returns hashed author_id.
    """
    return hash_author_id(platform, str(raw_identifier).strip(), salt)
