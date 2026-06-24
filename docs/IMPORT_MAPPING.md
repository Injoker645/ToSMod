# Import mapping

Profiles live in `config/import_profiles/*.yaml`.

## Dashboard flow

1. Open **Import**
2. Choose profile
3. Upload CSV/JSON/JSONL
4. Preview → Validate → Import

## Canonical columns

Required:
- `platform`
- `post_id`
- `comment_id`
- `text`

Optional:
- `label`, `severity`, `modality`
- `collection_stratum`, `search_query`
- `post_title`, `channel_name`, `url`

## Built-in profiles

- `tosmod_canonical`
- `apify_tiktok_export`
- `hf_sample`

## Templates

- `examples/templates/import_template.csv`
- `examples/data/demo_comments.csv`

## CLI

```powershell
python -m tosmod.import_cli --file data.csv --profile tosmod_canonical
```
