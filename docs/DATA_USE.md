# Data use policy

## What this repository includes

- **Synthetic demo data only** in `examples/data/`
- No real social media comments, handles, or annotated thesis corpus

## What you must not commit

- Real annotated exports (`annotations_final.csv` from private research)
- SQLite databases with collected comments
- API keys, session cookies, Instagram credentials
- DSA transparency bulk dumps with identifiable content

## Your datasets

When you import your own data locally:

- Data stays in `./data/` (gitignored)
- You are responsible for consent, ethics, and platform ToS compliance
- See `docs/DATA_SOURCES.md` for connector legal posture

## Licensed dataset release

A separately licensed research corpus may be published outside this repo. Link it from your model cards when available.
