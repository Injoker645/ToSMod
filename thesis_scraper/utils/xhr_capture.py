"""
Playwright route to capture comment/list and reply API responses.
Collects XHR responses for TikTok (and similar) comment APIs.
"""
import json
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

# Type alias for captured request/response
CapturedXHR = dict  # {"url": str, "method": str, "request_headers": dict, "response_body": str, "response_status": int}


def make_comment_list_capture_collector(
    url_pattern: str = "api/comment/list",
    reply_pattern: str = "api/comment/list/reply",
) -> tuple[Callable, List[CapturedXHR]]:
    """
    Returns (route_handler, list) where list is appended with captured responses.
    Use with page.route() or context.route().
    """
    captured: List[CapturedXHR] = []

    async def handle_route(route):
        request = route.request
        url = request.url
        if url_pattern not in url and reply_pattern not in url:
            await route.continue_()
            return
        try:
            response = await route.fetch()
            body = await response.body()
            captured.append({
                "url": url,
                "method": request.method,
                "response_status": response.status,
                "response_body": body.decode("utf-8", errors="replace"),
            })
            await route.fulfill(response=response)
        except Exception:
            await route.continue_()

    return handle_route, captured


def parse_comment_list_params(captured_url: str) -> dict:
    """Extract query params from a captured comment/list URL (e.g. TikTok)."""
    parsed = urlparse(captured_url)
    return dict(parse_qs(parsed.query, keep_blank_values=True))


def parse_comment_list_response(body: str) -> dict:
    """Parse JSON body of comment list API response."""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}
