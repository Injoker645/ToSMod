# Hugging Face model cards

Upload fine-tuned weights separately. Use these templates when publishing to the Hub.

| Template | Base model | Thesis headline metrics (exploratory, small-n) |
|----------|------------|--------------------------------------------------|
| [hatebert-7class-ted.md](hatebert-7class-ted.md) | GroNLP/hatebert | Binary F1 0.615, FPR ~10%, FNR ~31% |
| [roberta-7class-ted.md](roberta-7class-ted.md) | roberta-base | Binary F1 0.509 |
| [toxicbert-7class-ted.md](toxicbert-7class-ted.md) | unitary/toxic-bert | Binary F1 0.299 |

Train locally:

```bash
python -m training.finetune --db data/tosmod.db --base-model hatebert --variant T+ED --output-dir models/hatebert_ted
```

Then copy the corresponding model card to `README.md` in the Hub repo.
