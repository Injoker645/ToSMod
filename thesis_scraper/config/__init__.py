"""Config loading from settings.yaml and env."""
import os
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG: Optional[dict] = None


def _load_yaml(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "settings.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config(override_path: Optional[Path] = None) -> dict:
    """Load config from settings.yaml (cached)."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _load_yaml(override_path)
    return _CONFIG


def get_setting(key_path: str, default: Any = None) -> Any:
    """Get nested key, e.g. 'rate_limits.tiktok'."""
    data = get_config()
    for k in key_path.split("."):
        if not isinstance(data, dict):
            return default
        data = data.get(k)
    return default if data is None else data
