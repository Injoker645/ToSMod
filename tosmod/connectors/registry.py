"""Connector registry — loads config/connectors.yaml and checks env keys."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from tosmod.config.loader import get_config


class ConnectorRegistry:
    def __init__(self) -> None:
        self._cfg = get_config()
        raw = self._cfg.connectors.get("connectors", {})
        self._connectors: dict[str, dict[str, Any]] = raw if isinstance(raw, dict) else {}

    def list_connectors(self, tier: str | None = None, opt_in_enabled: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, spec in self._connectors.items():
            if not isinstance(spec, dict):
                continue
            t = spec.get("tier", "official")
            if tier and t != tier:
                continue
            if t == "opt_in" and not opt_in_enabled:
                spec = {**spec, "available": False, "reason": "opt_in_required"}
            else:
                requires = spec.get("requires", [])
                missing = [r for r in requires if not os.environ.get(r)]
                spec = {
                    **spec,
                    "id": key,
                    "available": len(missing) == 0,
                    "missing_env": missing,
                }
            out.append(spec)
        return out

    def get(self, connector_id: str) -> dict[str, Any] | None:
        spec = self._connectors.get(connector_id)
        if not isinstance(spec, dict):
            return None
        return {**spec, "id": connector_id}


@lru_cache(maxsize=1)
def get_registry() -> ConnectorRegistry:
    return ConnectorRegistry()
