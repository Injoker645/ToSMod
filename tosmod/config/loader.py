"""Load YAML config from the project config/ directory."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tosmod.paths import PROJECT_ROOT as _PROJECT_ROOT

_CONFIG_DIR = Path(os.environ.get("TOSMOD_CONFIG_DIR", str(_PROJECT_ROOT / "config")))


class ConfigLoader:
    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = Path(config_dir or _CONFIG_DIR)

    def _load_yaml(self, relative: str) -> dict[str, Any]:
        path = self.config_dir / relative
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    @property
    def taxonomy(self) -> dict[str, Any]:
        return self._load_yaml("taxonomy.yaml")

    @property
    def platforms(self) -> dict[str, Any]:
        return self._load_yaml("platforms.yaml")

    @property
    def defaults(self) -> dict[str, Any]:
        return self._load_yaml("defaults.yaml")

    @property
    def connectors(self) -> dict[str, Any]:
        return self._load_yaml("connectors.yaml")

    def label_names(self) -> list[str]:
        labels = self.taxonomy.get("labels", [])
        return [str(x["name"]) for x in labels if isinstance(x, dict) and "name" in x]

    def label_to_id(self) -> dict[str, int]:
        labels = self.taxonomy.get("labels", [])
        out: dict[str, int] = {}
        for item in labels:
            if isinstance(item, dict) and "name" in item and "id" in item:
                out[str(item["name"])] = int(item["id"])
        return out

    def id_to_label(self) -> dict[int, str]:
        return {v: k for k, v in self.label_to_id().items()}

    def modalities(self) -> list[str]:
        mods = self.taxonomy.get("modalities", [])
        return [str(m) for m in mods]

    def harmful_labels(self) -> set[str]:
        labels = self.taxonomy.get("labels", [])
        return {
            str(x["name"])
            for x in labels
            if isinstance(x, dict) and x.get("harmful")
        }

    def tos_guide(self, platform: str) -> dict[str, Any] | None:
        plat = (platform or "").lower().strip()
        if not plat:
            return None
        data = self._load_yaml(f"tos_guide/{plat}.yaml")
        return data if data else None

    def list_tos_platforms(self) -> list[str]:
        guide_dir = self.config_dir / "tos_guide"
        if not guide_dir.exists():
            return []
        return sorted(
            p.stem
            for p in guide_dir.glob("*.yaml")
            if p.stem != "_schema"
        )

    def db_path(self) -> Path:
        defaults = self.defaults
        db_cfg = defaults.get("database", {})
        env_key = db_cfg.get("env_override", "TOSMOD_DB_PATH")
        env_val = os.environ.get(env_key) or os.environ.get("THESIS_DB_PATH")
        if env_val:
            return Path(env_val)
        rel = db_cfg.get("path", "./data/tosmod.db")
        p = Path(rel)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return p

    def import_profile(self, name: str) -> dict[str, Any]:
        return self._load_yaml(f"import_profiles/{name}.yaml")


@lru_cache(maxsize=1)
def get_config() -> ConfigLoader:
    return ConfigLoader()
