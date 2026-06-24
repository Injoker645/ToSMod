# scripts

Automation and operator scripts at repo root scope (run from `c:\Uni\Thesis` unless noted).

| Script | Role |
|--------|------|
| `run_e6_sequence.py` | Sequential E6 multimodal pipeline (oracle, BLIP captions, multimodal eval) |
| `run_ollama_agreement_sweep.py` | Multi-model Ollama agreement runs |
| `run_queue.py` | Sequential HF trainer jobs from `experiments/job_queue.json` |
| `label_gifs.py` | Interactive GIF-description labeling |
| `build_oracle_from_labels.py` | Build oracle CSV for multimodal oracle condition |
| `build_oracle_placeholder.py` | Placeholder oracle |
| `export_annotations_csv.py`, `check_annotations.py`, `report_unlabeled.py` | Annotation QA |
| `remove_tiktok_placeholder_comments.py`, `delete_youtube_llm_silver.py` | Remove unusable TikTok `[sticker]`/`[empty]` rows; strip YouTube silver `llm` labels |
| `build_comment_engagement_features.py`, `backfill_thumbnail_cache.py`, `build_post_visual_context.py` | Materialise engagement ratios; cache thumbnails; **PaddleOCR** (optional GPU) + optional BLIP into `post_visual_context`. Legacy Tesseract reference: `scripts/legacy/tesseract_thumbnail_ocr_reference.py`, notes: `notebooks/0_data_exploration_tesseract_legacy.md` |
| `export_eda_corpus_profile.py` | Writes `docs/eda_corpus_profile.md` + `experiments/results/eda_corpus_profile.json` (class×platform, lengths, imbalance stats; factual) |
| `preflight_check.py`, `check_gif_urls.py` | Data checks |
| `latex_table_stats.py` | Stats for LaTeX tables |
| `watch_training.py`, `_check_progress.py` | Training monitoring |
| `instagram_login_session.py`, `instascrape_*.py`, `check_apify_schema.py` | Scraper / platform helpers |

See [`plans/handover_2.md`](../plans/handover_2.md) for full context and [`HANDOVER.md`](../HANDOVER.md) for Instagram session setup.
