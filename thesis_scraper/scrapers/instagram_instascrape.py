"""
Instagram Reels parent comments via authenticated GraphQL (InstaScrape method).

Uses cookie.json from InstaScrape-style login (sessionid, csrftoken, mid, ds_user_id).
Ref: https://github.com/kaifcodec/InstaScrape
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# InstaScrape GraphQL query for parent comments (shortcode_media -> edge_media_to_parent_comment)
PARENT_QUERY_HASH = "97b41c52301f77ce508f55e66d17620e"
COMMENTS_PER_PAGE = 50
GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
USER_AGENT = "Mozilla/5.0 (Linux; Android 13; SM-A125F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def read_instascrape_cookie(cookie_path: str) -> Optional[Dict[str, Any]]:
    """Read InstaScrape-format cookie.json; return None if missing/invalid."""
    p = _resolve_path(cookie_path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    cookies = data.get("cookies") or {}
    if not all(cookies.get(k) for k in ("sessionid", "csrftoken", "mid", "ds_user_id")):
        return None
    expiry = data.get("overall_expiry")
    if isinstance(expiry, (int, float)) and expiry <= __import__("time").time():
        logger.warning("InstaScrape cookie.json expired; re-run login.")
        return None
    return data


def _cookies_string(cookies: Dict[str, str]) -> str:
    return "sessionid={}; ds_user_id={}; csrftoken={}; mid={}".format(
        cookies.get("sessionid", ""),
        cookies.get("ds_user_id", ""),
        cookies.get("csrftoken", ""),
        cookies.get("mid", ""),
    )


def _build_headers(shortcode: str, cookies_str: str) -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "X-IG-App-ID": "936619743392459",
        "Referer": f"https://www.instagram.com/reel/{shortcode}/",
        "Cookie": cookies_str,
    }


async def _graphql_request(
    client: httpx.AsyncClient,
    query_hash: str,
    variables: Dict[str, Any],
    headers: Dict[str, str],
) -> Dict[str, Any]:
    var_str = json.dumps(variables, separators=(",", ":"))
    params = {"query_hash": query_hash, "variables": var_str}
    r = await client.get(GRAPHQL_URL, params=params, headers=headers, follow_redirects=False, timeout=20.0)
    if r.status_code in (301, 302, 303, 307, 308):
        raise RuntimeError("GraphQL redirected (auth may have expired).")
    if r.status_code == 401:
        raise RuntimeError("GraphQL 401 Unauthorized.")
    if r.status_code != 200:
        raise RuntimeError(f"GraphQL HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except Exception:
        raise RuntimeError("GraphQL response not JSON.")


def _parse_parent_comments(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """Return (list of comment dicts in our format, has_next_page, end_cursor)."""
    try:
        media = data["data"]["shortcode_media"]
        edge_info = media["edge_media_to_parent_comment"]
        edges = edge_info["edges"]
        page_info = edge_info["page_info"]
    except KeyError:
        raise RuntimeError("Unexpected GraphQL shape; missing comment edges.")
    out: List[Dict[str, Any]] = []
    for edge in edges:
        node = edge.get("node", {})
        cid = node.get("id")
        owner = node.get("owner", {})
        username = owner.get("username", "")
        out.append({
            "id": cid,
            "cid": cid,
            "text": node.get("text", ""),
            "digg_count": int(node.get("edge_liked_by", {}).get("count", 0) or 0),
            "reply_comment_total": int(node.get("edge_threaded_comments", {}).get("count", 0) or 0),
            "create_time": node.get("created_at"),
            "parent_comment_id": None,
            "username": username,
            "owner_id": str(owner.get("id", "")),
        })
    has_next = page_info.get("has_next_page", False)
    end_cursor = page_info.get("end_cursor")
    return out, has_next, end_cursor


async def fetch_parent_comments(
    shortcode: str,
    cookie_path: str,
    max_comments: Optional[int] = None,
    rps: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Fetch parent comments for an Instagram reel via authenticated GraphQL (InstaScrape method).
    cookie_path: path to InstaScrape-format cookie.json (from scripts/instascrape_login.py).
    """
    data = read_instascrape_cookie(cookie_path)
    if not data:
        logger.warning(
            "InstaScrape cookie not found or invalid at %s. Run: python scripts/instagram_login_session.py (creates cookie after login).",
            cookie_path,
        )
        return []
    cookies_str = _cookies_string(data["cookies"])
    headers = _build_headers(shortcode, cookies_str)
    all_comments: List[Dict[str, Any]] = []
    interval = 1.0 / max(rps, 0.5)
    next_cursor: Optional[str] = None

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(http2=True, timeout=httpx.Timeout(20.0, connect=10.0), limits=limits) as client:
        variables: Dict[str, Any] = {"shortcode": shortcode, "first": COMMENTS_PER_PAGE}
        try:
            data_resp = await _graphql_request(client, PARENT_QUERY_HASH, variables, headers)
        except RuntimeError as e:
            logger.warning("InstaScrape GraphQL request failed: %s", e)
            return []
        comments, has_next, end_cursor = _parse_parent_comments(data_resp)
        all_comments.extend(comments)
        next_cursor = end_cursor
        if max_comments and len(all_comments) >= max_comments:
            return all_comments[:max_comments]
        await asyncio.sleep(interval)
        while has_next and next_cursor:
            variables = {"shortcode": shortcode, "first": COMMENTS_PER_PAGE, "after": next_cursor}
            await asyncio.sleep(interval)
            try:
                data_resp = await _graphql_request(client, PARENT_QUERY_HASH, variables, headers)
            except RuntimeError as e:
                logger.warning("InstaScrape GraphQL pagination failed: %s", e)
                break
            comments, has_next, end_cursor = _parse_parent_comments(data_resp)
            all_comments.extend(comments)
            next_cursor = end_cursor
            if max_comments and len(all_comments) >= max_comments:
                break
    if max_comments:
        all_comments = all_comments[:max_comments]
    logger.info("InstaScrape: fetched %s parent comments for shortcode %s", len(all_comments), shortcode)
    return all_comments
