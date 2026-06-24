"""
Extract GIF/sticker image URLs from TikTok comment objects returned by Apify actors.

TikTok and actors use several shapes (imageList, imageListV2, nested sticker objects).
"""
from __future__ import annotations

from typing import Any, Optional


def _first_url_from_obj(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str) and obj.startswith("http"):
        return obj
    if isinstance(obj, dict):
        for key in ("urlList", "url_list", "urls", "url"):
            v = obj.get(key)
            if isinstance(v, list) and v:
                u = v[0]
                if isinstance(u, str) and u.startswith("http"):
                    return u
            if isinstance(v, str) and v.startswith("http"):
                return v
        nested = obj.get("imageURL") or obj.get("url") or obj.get("uri")
        return _first_url_from_obj(nested)
    if isinstance(obj, list):
        for el in obj:
            u = _first_url_from_obj(el)
            if u:
                return u
    return None


def _first_sticker_from_image_list(image_list: Any) -> tuple[Optional[str], Optional[str]]:
    if not image_list or not isinstance(image_list, list):
        return None, None
    first = image_list[0]
    if not isinstance(first, dict):
        return None, None
    url_obj = first.get("imageURL") or first.get("url") or first.get("uri") or first.get("downloadAddr")
    gif_url = _first_url_from_obj(url_obj) or _first_url_from_obj(first)
    gif_id = str(first.get("id") or first.get("sticker_id") or first.get("stickerId") or "") or None
    return gif_url, gif_id


def extract_apify_tiktok_comment_sticker(item: dict) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Return (has_gif, gif_url, gif_id) for multimodal comment rows.

    has_gif is True when we found at least one sticker/image URL (TikTok sticker replies).
    """
    if not isinstance(item, dict):
        return False, None, None

    for key in ("imageList", "imageListV2", "image_list", "imageResources", "picList", "pictures"):
        img_list = item.get(key)
        url, sid = _first_sticker_from_image_list(img_list)
        if url:
            return True, url, sid

    sticker = item.get("sticker") or item.get("stickers") or item.get("stickerInfo")
    if isinstance(sticker, dict):
        for key in ("imageList", "imageListV2", "images", "url", "picture", "staticUrl"):
            blob = sticker.get(key)
            if isinstance(blob, str) and blob.startswith("http"):
                return True, blob, str(sticker.get("id") or "") or None
            url, sid = _first_sticker_from_image_list(blob if isinstance(blob, list) else None)
            if url:
                return True, url, sid or str(sticker.get("id") or "") or None
        url = _first_url_from_obj(sticker)
        if url:
            return True, url, str(sticker.get("id") or "") or None

    for key in ("commentSticker", "emojiStickers", "stickerDetail", "media", "comment_media"):
        blob = item.get(key)
        url = _first_url_from_obj(blob)
        if url:
            return True, url, None

    return False, None, None
