# ToSMod

**Terms-of-Service-aware annotation and moderation research workbench.**

ToSMod is a self-hosted web application for collecting, annotating, and benchmarking social media content against a configurable harm taxonomy. It was developed as part of a master's thesis on automated content moderation across YouTube, TikTok, and Instagram, and has since been generalised into a standalone research tool anyone can run locally.

---

## Background

This project began as the technical infrastructure for a master's thesis investigating whether transformer-based classifiers can detect Terms-of-Service violations in social media comments with accuracy comparable to human annotators. The thesis annotated several thousand comments across three platforms using a seven-class harm taxonomy mapped to each platform's published ToS.

After completing the thesis, the workbench was cleaned up, generalised, and released as an open-source tool for other content moderation researchers who want:

- a labelling interface built around a structured harm taxonomy rather than free-form tags
- per-platform ToS guidance embedded into the annotation workflow
- a repeatable pipeline from data collection to model fine-tuning

The thesis dataset and annotations are not included. The tool ships with synthetic demo data and points to official data collection APIs.

---

## What makes it different

Generic annotation tools like Label Studio or Argilla are excellent for many NLP tasks, but they are not opinionated about content moderation. ToSMod is:

- **Taxonomy-driven.** The label schema, severity levels, and label descriptions live in `config/taxonomy.yaml` and can be edited without touching code.
- **ToS-aware.** The annotation panel shows the relevant clause from each platform's Terms of Service alongside the comment being labelled, so annotators can ground decisions in policy rather than intuition.
- **Cross-platform by design.** YouTube, TikTok, and Instagram data share a common schema; the same annotation interface and classifier training pipeline works across all three.
- **Self-contained.** A single SQLite database, a local `.env` for secrets, and one command to start the server. No cloud dependency for core functionality.

---

## Features

**Data collection**
- Search and collect from YouTube (official Data API), TikTok (Apify or TikTok Research API), Instagram (Apify)
- Import from CSV, JSON, or JSONL using configurable field-mapping profiles
- Import from Hugging Face datasets

**Annotation**
- Seven-class harm taxonomy with severity scoring
- Per-platform ToS clause shown alongside each comment
- Keyboard shortcuts for fast labelling
- Inter-rater agreement tooling

**Model training**
- Fine-tune HateBERT, RoBERTa, or ToxicBERT on your labelled data
- Hugging Face model card templates included
- Benchmark results from the original thesis available in `experiments/`

**Infrastructure**
- Config-driven: taxonomy, platform ToS guides, connectors, and import profiles are all YAML files
- Docker Compose for containerised deployment
- Local `.env` management through the Settings page in the dashboard

---

## Quick start

**Option 1: Double-click launcher (Windows)**

Double-click `Launch-ToSMod.bat` in the project folder and choose:
- `1` Quick launch (install dependencies and start)
- `2` Full launch (install, seed demo data, run tests, start)
- `3` Docker launch

**Option 2: Command line**

```powershell
cd C:\path\to\ToSMod
python -m pip install -e ".[dev]"
python -m tosmod seed        # populate demo database
python -m tosmod test        # verify installation
python -m tosmod serve       # start dashboard at http://127.0.0.1:5050
```

**Option 3: Docker**

```bash
cp .env.example .env         # add your API keys
docker compose up --build
```

Open `http://127.0.0.1:5050` in your browser.

---

## First-time setup

1. Open the **Settings** tab and add any API keys you have.
   - Only `ANONYMIZATION_SALT` is strictly required (generate one with the button).
   - Data collection keys are optional; the tool works for annotation with imported data only.
2. Click **Verify connectors** to check which collection paths are available.
3. If you have no data yet, click **Seed Demo** to load synthetic example data.
4. Use **Search** or **Import** to bring in real data.
5. Label in **Annotate**.
6. Use **Experiments** when you are ready to train or benchmark a classifier.

---

## Repository layout

```
config/               YAML configuration: taxonomy, ToS guides, connectors, import profiles
tosmod/               Python package: config loader, import engine, connector registry
  connectors/         Official and opt-in data collectors
  import_/            CSV/JSON/JSONL import with field mapping
dashboard/            Flask web application
  templates/          Single-page HTML/JS interface
  app.py              Core API routes
  tosmod_routes.py    Settings, import, and connector API blueprint
thesis_scraper/       Legacy scraper package (wrapped by tosmod.connectors)
training/             Fine-tuning CLI for transformer models
experiments/          Experiment code and aggregated results from the thesis
model_cards/          Hugging Face model card templates
examples/             Synthetic demo data and import templates
docs/                 User and developer documentation
```

---

## Configuration

All behaviour is controlled by files in `config/`. No code changes are needed to:

- Add or rename harm labels: edit `config/taxonomy.yaml`
- Update ToS guidance text: edit `config/tos_guide/<platform>.yaml`
- Add a new import profile: add `config/import_profiles/<name>.yaml`
- Register a new connector: add an entry to `config/connectors.yaml`

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) and [docs/IMPORT_MAPPING.md](docs/IMPORT_MAPPING.md) for details.

---

## Documentation

| File | Contents |
|---|---|
| [docs/SECRETS.md](docs/SECRETS.md) | API key setup and `.env` management |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Connector options per platform |
| [docs/IMPORT_MAPPING.md](docs/IMPORT_MAPPING.md) | Import profiles, canonical columns, CLI usage |
| [docs/ANNOTATION.md](docs/ANNOTATION.md) | Annotation workflow and keyboard shortcuts |
| [docs/DATA_USE.md](docs/DATA_USE.md) | Data ethics and compliance guidance |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to extend the taxonomy, add platforms, contribute code |
| [ROADMAP.md](ROADMAP.md) | Phase history and planned work |

---

## Legal and ethics

ToSMod ships official-API connectors for YouTube, TikTok Research, and Reddit. Apify-based collectors for TikTok and Instagram are available as opt-in modules and must be enabled explicitly (`TOSMOD_ENABLE_OPT_IN=1`). Use of these collectors is your responsibility; see [tosmod/connectors/opt_in/LEGAL.md](tosmod/connectors/opt_in/LEGAL.md).

Do not commit real user data, annotation exports, or API keys to any public fork of this repository.

---

## Citation

If you use ToSMod or the thesis benchmark results in academic work, please cite:

```bibtex
@software{tosmod,
  author  = {Islam, Moaaz Tameer},
  title   = {ToSMod: Terms-of-Service-aware annotation and moderation research workbench},
  year    = {2025},
  url     = {https://github.com/Injoker645/ToSMod},
  license = {MIT}
}
```

---

## License

[MIT](LICENSE)
