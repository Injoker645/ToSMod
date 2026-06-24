# Secrets and settings

ToSMod uses a local `.env` in the repo root.

## Preferred workflow

1. Open **Settings** in the dashboard
2. Enter keys/secrets
3. Click **Save settings**
4. Click **Verify connectors**

This updates only local `.env` for this folder.

## Manual setup

```powershell
cd C:\Uni\ToSMod
copy .env.example .env
```

## Key variables

- `ANONYMIZATION_SALT` (required, generate once)
- `TOSMOD_DB_PATH`
- `YOUTUBE_API_KEY`
- `APIFY_API_KEY`
- `TIKTOK_RESEARCH_CLIENT_KEY`
- `TIKTOK_RESEARCH_CLIENT_SECRET`
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`
- `TOSMOD_ENABLE_OPT_IN`

Generate salt:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
