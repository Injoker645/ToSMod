# ToSMod documentation

This folder contains user and developer documentation for ToSMod, a self-hosted annotation and moderation research workbench.

For project background and quick start, see the [top-level README](../README.md).

---

## User guides

| File | Contents |
|---|---|
| [MODELS.md](MODELS.md) | Model selection, thesis benchmark results, architecture breakdown |
| [SECRETS.md](SECRETS.md) | How to set up API keys and manage your `.env` file |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Available data connectors, which keys they require, and compliance notes |
| [IMPORT_MAPPING.md](IMPORT_MAPPING.md) | Importing CSV/JSON/JSONL files, canonical columns, built-in profiles |
| [ANNOTATION.md](ANNOTATION.md) | Annotation interface layout, workflow, and keyboard shortcuts |
| [DATA_USE.md](DATA_USE.md) | Data ethics, platform Terms of Service, and responsible use guidance |

---

## Developer and architecture references

| File | Contents |
|---|---|
| [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) | Architecture overview, phase history, recent changes |
| [data_pipeline_architecture.md](data_pipeline_architecture.md) | End-to-end data flow from collection to annotation |
| [experiments_overview.md](experiments_overview.md) | Experiment structure inherited from the thesis |

---

## Background: from thesis to open-source tool

ToSMod started as the technical infrastructure of a master's thesis on automated content moderation. The thesis investigated whether transformer classifiers (HateBERT, RoBERTa, ToxicBERT) can detect Terms-of-Service violations in social media comments at a level comparable to human annotators, across YouTube, TikTok, and Instagram.

The annotation workbench, data collection scripts, classifier training pipeline, and experiment framework built for the thesis have been generalised and released here so other researchers can use and extend them. The original annotated corpus and thesis text are not included; only the tooling and aggregated results are published.

---

## What is not in this repository

- Real user comments or annotation exports from the thesis corpus
- Draft thesis text or internal planning documents
- API credentials or secrets of any kind
- Personal account handles or identifiable user data

Synthetic demo data is available in `examples/` and can be loaded with `python -m tosmod seed`.
