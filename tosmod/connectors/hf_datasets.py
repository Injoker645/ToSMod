"""Hugging Face datasets loader — hands rows to import mapping engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tosmod.connectors.base import BaseConnector
from tosmod.import_.engine import ImportEngine


class HFDatasetsConnector(BaseConnector):
    connector_id = "hf_datasets"
    platform = "custom"

    def is_configured(self) -> bool:
        return True

    def collect_comments(self, target: str, **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError("Use import with --hf-dataset and a mapping profile instead")

    def load_to_sqlite(
        self,
        conn,
        dataset_name: str,
        split: str = "train",
        profile: dict[str, Any] | None = None,
        limit: int | None = None,
        profile_path: Path | None = None,
    ):
        try:
            from datasets import load_dataset
        except ImportError as e:
            raise RuntimeError("Install datasets: pip install datasets") from e
        ds = load_dataset(dataset_name, split=split)
        import tempfile
        import csv

        engine = ImportEngine()
        prof = profile or {}
        if profile_path:
            import yaml
            with profile_path.open(encoding="utf-8") as f:
                prof = yaml.safe_load(f) or {}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="") as tf:
            if ds.column_names:
                writer = csv.DictWriter(tf, fieldnames=ds.column_names)
                writer.writeheader()
                for i, row in enumerate(ds):
                    if limit and i >= limit:
                        break
                    writer.writerow({k: row[k] for k in ds.column_names})
            tmp = Path(tf.name)
        return engine.import_file(conn, tmp, prof, limit=limit)
