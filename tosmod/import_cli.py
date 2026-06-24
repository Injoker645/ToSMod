#!/usr/bin/env python3
"""CLI for dataset import with mapping profiles."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from thesis_scraper.storage.database import init_schema
from tosmod.config.loader import get_config
from tosmod.connectors.hf_datasets import HFDatasetsConnector
from tosmod.import_.engine import ImportEngine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path)
    ap.add_argument("--profile", default="tosmod_canonical")
    ap.add_argument("--hf-dataset")
    ap.add_argument("--split", default="train")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    cfg = get_config()
    db_path = cfg.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)

    profile = cfg.import_profile(args.profile)
    if not profile:
        raise SystemExit(f"Profile not found: {args.profile}")

    if args.hf_dataset:
        HFDatasetsConnector().load_to_sqlite(
            conn, args.hf_dataset, split=args.split, profile=profile, limit=args.limit
        )
        print(f"Imported HF dataset {args.hf_dataset}")
    elif args.file:
        result = ImportEngine().import_file(conn, args.file, profile, limit=args.limit)
        print(result)
    else:
        raise SystemExit("Provide --file or --hf-dataset")
    conn.close()


if __name__ == "__main__":
    main()
