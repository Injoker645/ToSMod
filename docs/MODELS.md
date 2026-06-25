# Models

This document describes the three base models available in ToSMod, the benchmark results from the thesis that motivated the tool, and practical guidance on which model to use for your own annotation corpus.

For fine-tuning instructions, see the [Train & Evaluate tab](../training/README.md) in the dashboard or run `python -m training.finetune --help`.

---

## Background

The thesis *"Cracks in the Feed"* (Uppsala University, 2026) fine-tuned three encoder models on a corpus of 2,564 human-labelled comments from YouTube Shorts, TikTok, and Instagram Reels. Comments were labelled across seven harm classes mapped to each platform's published Terms of Service. The benchmark used a stratified train/val/test split (test set: 451 comments, human gold labels only).

All three models were tested with three input text variants:

| Variant | Description |
|---|---|
| `T` | Plain text only |
| `T+E` | Text with emoji characters preserved |
| `T+ED` | Text with emoji demojized to words (e.g. `face_with_tears_of_joy`) |

The `T+ED` variant consistently performed best or matched best for all three backbones.

---

## Available models

### HateBERT

**HuggingFace:** [GroNLP/hatebert](https://huggingface.co/GroNLP/hatebert)

HateBERT is a BERT-base encoder that underwent continued masked language model pre-training on 1.5M Reddit comments from communities that were banned for violating Reddit's rules (r/MGTOW, r/WhiteRights, etc.). This domain adaptation gives it stronger priors for implicit and community-specific harm language — exactly the type of comment that evades keyword filters.

**When to use:** First choice for comment-level harm detection. Its pre-training on hate-adjacent Reddit text gives it the best signal for comments that use euphemism, implication, or in-group language.

**Thesis results (E3 fine-tuning, test n=451):**

| Input variant | Binary F1 | Macro F1 | FPR | FNR |
|---|---|---|---|---|
| T | 0.595 | 0.336 | 0.108 | 0.329 |
| T+E | 0.573 | 0.364 | 0.110 | 0.357 |
| **T+ED** | **0.615** | **0.339** | **0.100** | **0.314** |

HateBERT with `T+ED` is the best-performing configuration overall: highest binary F1, lowest FPR (10%), lowest FNR (31.4%).

**Confusion matrix (best row: HateBERT T+ED):**

|  | Predicted Safe | Predicted Harmful |
|---|---|---|
| Actual Safe | 343 | 38 |
| Actual Harmful | 22 | 48 |

**Per-class breakdown (best row):**

| Class | Test count | FNR | FPR | F1 |
|---|---|---|---|---|
| Safe | 381 | 0.100 | 0.314 | 0.920 |
| Harassment | 6 | 1.000 | 0.000 | 0.000 |
| Hate | 10 | 0.800 | 0.009 | 0.250 |
| Self-harm | 7 | 0.429 | 0.023 | 0.381 |
| Sexual | 29 | 0.276 | 0.059 | 0.560 |
| Dehumanisation | 18 | 0.722 | 0.035 | 0.263 |

The per-class numbers reflect two structural issues: the corpus is heavily safe-skewed, and rare harm classes (Harassment: 6 test examples) are structurally hard to separate with this corpus size regardless of model quality. Sexual harm, with the most support among harmful classes, achieves the best per-class F1 (0.560).

---

### RoBERTa

**HuggingFace:** [roberta-base](https://huggingface.co/roberta-base)

RoBERTa-base is a robustly trained BERT variant with dynamic masking and larger batch pre-training. It has no domain-specific pre-training for hate speech, making it a strong general-purpose baseline but a weaker starting point than HateBERT for comment moderation specifically.

**When to use:** When you want a well-calibrated general encoder, or when your harm labels go beyond social media comment harm (e.g. misinformation in news text, policy violations in user profiles). Also useful for cross-domain experiments where HateBERT's Reddit priors might not transfer.

**Thesis results (E3 fine-tuning, test n=451):**

| Input variant | Binary F1 | Macro F1 | FPR | FNR |
|---|---|---|---|---|
| T | 0.512 | 0.357 | 0.144 | 0.386 |
| T+E | 0.531 | **0.374** | **0.129** | 0.386 |
| T+ED | **0.549** | 0.365 | **0.129** | **0.357** |

RoBERTa achieves higher macro F1 in some configurations (0.374 with T+E) than HateBERT, meaning it distributes its errors more evenly across classes. Its binary F1 is lower — it misses more harmful comments overall.

---

### ToxicBERT

**HuggingFace:** [unitary/toxic-bert](https://huggingface.co/unitary/toxic-bert)

ToxicBERT is a BERT-base model fine-tuned on the Jigsaw Unintended Bias in Toxicity Classification dataset (Wikipedia comment corpus, ~1.8M rows). Unlike HateBERT's pre-training approach, ToxicBERT was directly fine-tuned on toxicity labels — which means it brings an existing toxicity head that gets replaced when you fine-tune on your own labels, but its internal representations already encode toxicity-adjacent signals.

**When to use:** When your corpus has a high proportion of explicit toxicity (slurs, direct threats, graphic language) rather than implicit or coded harm. ToxicBERT tends toward high recall on obvious harm at the expense of precision.

**Thesis results (E3 fine-tuning, test n=451):**

| Input variant | Binary F1 | Macro F1 | FPR | FNR |
|---|---|---|---|---|
| T | **0.565** | **0.355** | **0.136** | **0.314** |
| T+E | **0.565** | **0.355** | **0.136** | **0.314** |
| T+ED | 0.543 | 0.343 | 0.147 | 0.329 |

ToxicBERT's T and T+E variants match each other exactly in these results, suggesting the model's signal comes primarily from token identity rather than emoji handling. The `T+ED` variant actually performs worse — demojizing may introduce noise that conflicts with Jigsaw training distribution.

---

## Cross-platform transfer

One of the thesis's clearest negative findings was that models do not transfer across platforms with this corpus. A leave-one-platform-out (LOPO) experiment trained HateBERT on two platforms and tested on the third. In all configurations, the model assigned near-100% of safe comments as harmful on the held-out platform (FPR 98–100%), a degenerate operating point.

This means: **if you collect from multiple platforms, train a separate model per platform or ensure your training data represents the target platform's distribution.** A model trained on YouTube comments should not be applied to Instagram comments without re-fine-tuning.

---

## Comparison with bag-of-words baseline

For context, the TF-IDF + SVM baseline (E2) achieved binary F1 of 0.119 — essentially random on harmful comments. The best transformer (HateBERT T+ED) at 0.615 represents a substantial improvement, but the absolute number is a reminder that comment-level harm detection with limited labelled data is genuinely hard. The models work; they are not production-ready classifiers.

---

## Multimodal and context extensions

The thesis also tested:

- **Post thumbnail context (E6):** Prepending BLIP-2 captions from the video thumbnail to the comment text. Thumbnail context barely moved overall F1 but raised false positives on comments beneath visually ambiguous thumbnails. Not recommended as a default.
- **GIF/image comment context (E7):** Manually described GIF content prepended to the comment. Results were mixed — some GIF-based harm became detectable, but the pipeline depends on manual or VLM descriptions that add latency and noise.

Both extensions are available in `training/finetune.py` via the `--variant` and context fields in the database.

---

## Practical recommendations

| Scenario | Recommended model | Input variant |
|---|---|---|
| General comment harm detection | HateBERT | T+ED |
| Cross-domain or non-social-media text | RoBERTa | T+ED |
| Corpus with high explicit toxicity | ToxicBERT | T or T+E |
| Limited GPU / quick smoke test | Any, with `--quick` flag | T |
| Multi-platform corpus | Train one model per platform | T+ED |

Fine-tuning requires training dependencies: `pip install -e ".[training]"`. On a single GPU the `--quick` flag runs a minimal epoch pass useful for verifying the pipeline before a full run.

---

## Model cards

Hugging Face model card templates for all three backbones are in [model_cards/](../model_cards/). They include the intended use, training data description, known limitations, and the binary F1/FPR/FNR figures from the thesis. If you publish a fine-tuned model derived from this work, fill in the template and push it alongside your model weights.
