"""
Optional proxy rotation (config-driven).
"""
from typing import Optional


def get_proxy_config(
    enabled: bool = False,
    residential_proxy_url: Optional[str] = None,
    rotate_per_request: bool = False,
) -> Optional[dict]:
    """
    Return Playwright-style proxy config if enabled.
    Example: {"server": "http://proxy:port", "username": "u", "password": "p"}
    """
    if not enabled or not residential_proxy_url:
        return None
    # Assume URL format http://user:pass@host:port or http://host:port
    server = residential_proxy_url
    username = None
    password = None
    if "@" in residential_proxy_url:
        auth, server = residential_proxy_url.rsplit("@", 1)
        if "://" in auth:
            prefix, rest = auth.split("://", 1)
            if ":" in rest:
                username, password = rest.split(":", 1)
        else:
            if ":" in auth:
                username, password = auth.split(":", 1)
    out = {"server": server if "://" in server else f"http://{server}"}
    if username:
        out["username"] = username
    if password:
        out["password"] = password
    return out
