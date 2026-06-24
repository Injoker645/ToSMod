# Contributing to ToSMod

Thank you for your interest. ToSMod is a research tool that grew out of a master's thesis on content moderation. Contributions that improve its usefulness for other researchers are welcome.

---

## What is in scope

- Bug fixes in the dashboard, import engine, or connectors
- New import profiles for common dataset formats
- New official-API connectors (those that do not violate platform ToS)
- Improvements to the annotation interface
- Documentation and translation improvements
- Additional classifier architectures in `training/`

## What is out of scope

- Scrapers that automate access to platforms that prohibit it (Instagram, TikTok) as built-in features. These belong in the opt-in module at `tosmod/connectors/opt_in/` with explicit user acknowledgement.
- Changes that embed real user data, personal account handles, or thesis-specific content.

---

## How to extend the taxonomy

1. Edit `config/taxonomy.yaml` to add, rename, or remove harm classes and severity levels.
2. Update the matching entries in each `config/tos_guide/<platform>.yaml` so annotators see accurate guidance for the new label.
3. Restart the dashboard. Changes take effect immediately.

## How to add a platform

1. Add an entry to `config/platforms.yaml` with the platform name and display colour.
2. Create `config/tos_guide/<platform>.yaml` with guidance text for each harm class.
3. Optionally add a connector entry in `config/connectors.yaml`.
4. Restart the dashboard.

## How to add an import profile

1. Create `config/import_profiles/<name>.yaml` following the format of existing profiles.
2. Test using the Import tab: Preview, Validate, then Import.
3. Document the expected source format in a comment at the top of the YAML.

## How to add a connector

1. Create `tosmod/connectors/<name>.py` inheriting from `tosmod.connectors.base.BaseConnector`.
2. Register it in `config/connectors.yaml`.
3. If it requires opt-in (e.g. Apify-based scraping), place it under `tosmod/connectors/opt_in/` and add a disclaimer.

---

## Development setup

```powershell
cd C:\path\to\ToSMod
python -m pip install -e ".[dev]"
python -m tosmod seed
python -m tosmod test
python -m tosmod serve
```

The test suite is in `tests/`. Run it with `python -m tosmod test` or `pytest`.

---

## Code style

- Match the conventions in the existing Flask routes and `thesis_scraper` package.
- Keep pull requests focused on one concern. Large refactors should be discussed in an issue first.
- Do not commit `.env`, API keys, or any real user data.

## Pull requests

- Reference the relevant issue if one exists.
- Update `docs/PROJECT_CONTEXT.md` if your change affects architecture or configuration.
- Update `ROADMAP.md` if a planned phase is completed or a new one is added.
- All new routes should have at least a smoke test in `tests/test_tosmod.py`.

---

## Reporting issues

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce (without including API keys or personal data)

For security issues, see [SECURITY.md](SECURITY.md).
