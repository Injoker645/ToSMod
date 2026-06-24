# Project context

This document describes the architecture of ToSMod, its origin, and the current state of the codebase. It is intended for developers and contributors rather than end users.

---

## Origin

ToSMod was built as the technical infrastructure for a master's thesis on automated content moderation. The thesis annotated several thousand social media comments across YouTube, TikTok, and Instagram using a seven-class harm taxonomy derived from each platform's published Terms of Service. Three transformer classifiers (HateBERT, RoBERTa, ToxicBERT) were fine-tuned on the annotated data and benchmarked for F1, false positive rate, and false negative rate per platform.

After the thesis was complete, the workbench was cleaned up and generalised:

- The taxonomy, ToS guidance text, platform definitions, and import profiles were moved from hardcoded values into YAML configuration files.
- The scraping code was wrapped behind a connector registry with tiering (official API vs opt-in).
- The annotation interface was retained and extended with a Settings page, onboarding flow, and improved Import UI.
- Sensitive data (the annotated corpus, thesis drafts, internal plans) was excluded. Synthetic demo data was added.

---

## Architecture

```
config/
  taxonomy.yaml           Harm class definitions and severity levels
  platforms.yaml          Platform display names and colours
  tos_guide/              Per-platform ToS guidance text per harm class
  connectors.yaml         Connector registry (name, tier, keys required)
  import_profiles/        Field-mapping profiles for CSV/JSON/JSONL import
  defaults.yaml           Default runtime settings

tosmod/
  config/loader.py        Loads and caches YAML config at startup
  connectors/             Connector implementations
    base.py               Abstract base class
    registry.py           Loads connectors from config
    youtube_api.py
    tiktok_research.py
    reddit_api.py
    apify.py
    hf_datasets.py
    opt_in/               Opt-in (potentially ToS-violating) scrapers
  import_/engine.py       CSV/JSON/JSONL import with field mapping
  import_cli.py           CLI entry point for headless import
  seed.py                 Demo data seeder
  cli.py                  `tosmod seed|test|serve` entry point
  paths.py                PROJECT_ROOT resolution

dashboard/
  app.py                  Flask application and core API routes
  tosmod_routes.py        Blueprint: settings, import, connectors
  templates/index.html    Single-page interface (HTML + vanilla JS)

thesis_scraper/           Legacy scraper package from the thesis
                          Being progressively wrapped by tosmod.connectors

training/
  finetune.py             Fine-tune CLI for transformer models

experiments/              Experiment scripts and aggregated results

model_cards/              Hugging Face model card templates

examples/
  data/                   Synthetic demo CSVs
  templates/              Import template CSV
```

---

## Data flow

1. **Collect** via Search tab (YouTube/TikTok/Instagram) or Import tab (CSV/JSON/JSONL/HF dataset).
2. Posts and comments are written to a SQLite database (`data/tosmod.db` by default, configurable via `TOSMOD_DB_PATH`).
3. **Annotate** in the Annotate tab. Labels are stored in the same database.
4. **Export** annotations from the Database tab for use in model training.
5. **Train** using `training/finetune.py` or the Experiments tab wrapper.
6. **Benchmark** results are stored back into the database and shown in the Experiments tab.

---

## Configuration loading

`tosmod.config.loader.get_config()` returns a cached `ToSModConfig` object. It reads all YAML files from `config/` at first call. The dashboard calls this at startup and passes configuration to template rendering and to the connector registry.

To change taxonomy labels or ToS guidance without a code change, edit the relevant YAML and restart the dashboard.

---

## Settings and secrets

The Settings tab in the dashboard writes to a local `.env` file in the project root. This file is excluded from version control via `.gitignore`. The `_read_env_file` / `_write_env_file` helpers in `tosmod_routes.py` handle parsing and updating individual keys without clobbering unrelated variables.

All secret values returned by the settings API are masked (first four characters shown, remainder replaced).

---

## Phase history

| Phase | Contents |
|---|---|
| 0 | Sanitized repository, MIT license, synthetic demo seed, `pytest.ini`, `pyproject.toml` |
| 1 | Config-driven taxonomy + ToS YAML, dashboard loads config at runtime |
| 2 | Docker Compose, split requirements files, `SECRETS.md` |
| 3 | `training/finetune.py`, Hugging Face model card templates |
| 4 | Connector registry, official vs opt-in tiering, `LEGAL.md` for opt-in |
| 5 | Import mapping engine, configurable profiles, Import UX, Settings page, onboarding banner |

---

## What is excluded from this repository

- `data/` — the real annotated corpus from the thesis
- `latex_report/` — thesis draft text
- `plans/` — internal planning documents
- `.env` — API keys and secrets
- Any real user handles, comments, or other personal data

Synthetic replacements for the real data are in `examples/`.
