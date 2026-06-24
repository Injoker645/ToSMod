"""
Fine-tune HateBERT / RoBERTa / ToxicBERT on ToSMod taxonomy labels.

Usage:
  python -m training.finetune --csv examples/data/demo_comments.csv --base-model hatebert --variant T+ED --output-dir models/demo --quick
  python -m training.finetune --db data/tosmod.db --base-model hatebert --variant T
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tosmod.config.loader import get_config

CHECKPOINTS = {
    "roberta": "roberta-base",
    "hatebert": "GroNLP/hatebert",
    "toxicbert": "unitary/toxic-bert",
}


def _variant(text: str, variant: str) -> str:
    if variant == "T":
        return " ".join((text or "").split())
    if variant == "T+E":
        return (text or "").strip()
    if variant == "T+ED":
        try:
            import emoji as emoji_lib
            return emoji_lib.demojize((text or "").strip(), delimiters=(" ", " "))
        except ImportError:
            return (text or "").strip()
    raise ValueError(variant)


def load_from_csv(csv_path: Path, variant: str, label_to_id: dict[str, int]):
    texts, labels = [], []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            label = (row.get("label") or "").upper()
            if label not in label_to_id:
                continue
            texts.append(_variant(row.get("text", ""), variant))
            labels.append(label_to_id[label])
    return texts, labels


def load_from_db(db_path: Path, variant: str, label_to_id: dict[str, int]):
    import sqlite3
    from thesis_scraper.storage.database import init_schema

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT c.text, a.label FROM annotations a
        JOIN comments c ON c.platform=a.platform AND c.post_id=a.post_id AND c.comment_id=a.comment_id
        WHERE a.label_source IN ('human', 'import')
        """
    ).fetchall()
    conn.close()
    texts, labels = [], []
    for r in rows:
        label = (r["label"] or "").upper()
        if label not in label_to_id:
            continue
        texts.append(_variant(r["text"] or "", variant))
        labels.append(label_to_id[label])
    return texts, labels


def main() -> None:
    ap = argparse.ArgumentParser(description="ToSMod transformer fine-tune")
    ap.add_argument("--csv", type=Path, help="Labeled CSV with text,label columns")
    ap.add_argument("--db", type=Path, help="SQLite DB path (alternative to --csv)")
    ap.add_argument("--base-model", default="hatebert", choices=list(CHECKPOINTS.keys()))
    ap.add_argument("--variant", default="T+ED", choices=["T", "T+E", "T+ED"])
    ap.add_argument("--output-dir", type=Path, default=ROOT / "models" / "tosmod_run")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="1 epoch, small batch, truncated data")
    args = ap.parse_args()

    cfg = get_config()
    label_to_id = cfg.label_to_id()
    num_labels = len(label_to_id)

    if args.csv:
        texts, labels = load_from_csv(args.csv, args.variant, label_to_id)
    elif args.db:
        texts, labels = load_from_db(args.db, args.variant, label_to_id)
    else:
        db = cfg.db_path()
        if db.exists():
            texts, labels = load_from_db(db, args.variant, label_to_id)
        else:
            raise SystemExit("Provide --csv or --db, or seed demo DB first")

    if len(texts) < 10:
        raise SystemExit(f"Need at least 10 labeled rows; got {len(texts)}")

    if args.quick:
        texts, labels = texts[: min(64, len(texts))], labels[: min(64, len(labels))]
        args.epochs = 1

    import numpy as np
    import torch
    from sklearn.model_selection import train_test_split
    from torch.utils.data import Dataset
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
    )

    class _DS(Dataset):
        def __init__(self, texts, labels, tokenizer, max_len=128):
            self.texts, self.labels, self.tokenizer, self.max_len = texts, labels, tokenizer, max_len

        def __len__(self):
            return len(self.texts)

        def __getitem__(self, i):
            enc = self.tokenizer(
                self.texts[i], truncation=True, max_length=self.max_len,
                padding="max_length", return_tensors="pt",
            )
            item = {k: v.squeeze(0) for k, v in enc.items()}
            item["labels"] = torch.tensor(self.labels[i], dtype=torch.long)
            return item

    X_train, X_val, y_train, y_val = train_test_split(
        texts, labels, test_size=0.15, random_state=42, stratify=labels if len(set(labels)) > 1 else None
    )

    model_name = CHECKPOINTS[args.base_model]
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=num_labels)

    train_ds = _DS(X_train, y_train, tokenizer)
    val_ds = _DS(X_val, y_val, tokenizer)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=2 if args.quick else 16,
        per_device_eval_batch_size=16,
        learning_rate=2e-5,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        logging_steps=10,
        fp16=torch.cuda.is_available(),
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        tokenizer=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print(f"Saved model to {args.output_dir}")


if __name__ == "__main__":
    main()
