# Training

## Fine-tune from demo CSV

```bash
pip install -r requirements-training.txt
python -m training.finetune --csv examples/data/demo_comments.csv --base-model hatebert --variant T+ED --output-dir models/demo --quick
```

## Fine-tune from SQLite

```bash
python scripts/seed_demo_db.py
python -m training.finetune --db data/tosmod.db --base-model hatebert --variant T+ED --output-dir models/hatebert_ted
```

## Upload to Hugging Face Hub

Requires `huggingface-cli login` and trained weights in `models/hatebert_ted/`.

```bash
pip install huggingface_hub
huggingface-cli upload your-username/tosmod-hatebert-7class-ted models/hatebert_ted --commit-message "ToSMod HateBERT T+ED"
```

Copy the matching file from `model_cards/hatebert-7class-ted.md` to the Hub repo README.

## Baselines

Full thesis baselines remain in `experiments/02_text_classification/baselines.py`.
