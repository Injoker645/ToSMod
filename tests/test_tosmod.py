"""Basic tests for ToSMod config and import."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config_dir():
    return ROOT / "config"


def test_taxonomy_loads(config_dir):
    from tosmod.config.loader import ConfigLoader
    cfg = ConfigLoader(config_dir)
    names = cfg.label_names()
    assert "SAFE" in names
    assert len(names) == 7


def test_tos_guide_youtube(config_dir):
    from tosmod.config.loader import ConfigLoader
    guide = ConfigLoader(config_dir).tos_guide("youtube")
    assert guide is not None
    assert "labels" in guide
    assert "HARASS" in guide["labels"]


def test_import_engine_csv(config_dir):
    from tosmod.config.loader import ConfigLoader
    from tosmod.import_.engine import ImportEngine
    from thesis_scraper.storage.database import init_schema

    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "t.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["platform", "post_id", "comment_id", "text", "label"])
            w.writeheader()
            w.writerow({"platform": "custom", "post_id": "p1", "comment_id": "c1", "text": "hello", "label": "SAFE"})
        profile_path = config_dir / "import_profiles" / "tosmod_canonical.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        db = Path(td) / "t.db"
        conn = sqlite3.connect(str(db))
        init_schema(conn)
        result = ImportEngine(ConfigLoader(config_dir)).import_file(conn, csv_path, profile)
        assert result.comments_upserted == 1
        conn.close()


def test_connector_registry():
    from tosmod.connectors.registry import ConnectorRegistry
    reg = ConnectorRegistry()
    items = reg.list_connectors()
    assert any(c.get("id") == "youtube_official" for c in items)
