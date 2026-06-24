---
license: mit
base_model: GroNLP/hatebert
tags:
  - text-classification
  - content-moderation
  - harm-detection
---

# tosmod/hatebert-7class-ted

Fine-tuned HateBERT for ToSMod's seven-class harm taxonomy (SAFE, HARASS, HATE, SELFHARM, SEXUAL, DEHUMANISE, MISINFO).

## Intended use

Research benchmarking of short-form video comment moderation. **Not** for production deployment without further validation.

## Training

- Base: `GroNLP/hatebert`
- Input variant: T+ED (emoji demojized)
- Labels: 7-class single-label classification

## Limitations

- Trained on a small exploratory corpus (thesis n≈451 test split)
- English/Swedish mix; platform bias (YouTube, TikTok, Instagram)
- Metrics are exploratory ceilings, not production guarantees

## Metrics (thesis stratified test, exploratory)

| Metric | Value |
|--------|-------|
| Binary F1 | 0.615 |
| FPR | ~10% |
| FNR | ~31% |

## Data

Training data is **not** included in this repository. Use your own labeled corpus via ToSMod import.

## Ethical considerations

Identity-substitution bias probe (E8) documented in thesis — models may flip labels on identity suffixes.
