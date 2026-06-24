# Data sources

## Search tab platforms

- **YouTube**: official Data API flow
- **TikTok**: Apify hashtag discovery or single URL collection
- **Instagram**: Apify hashtag discovery or single URL collection

## Connector tiers

### Official/default

- `youtube_official` (`YOUTUBE_API_KEY`)
- `tiktok_research` (`TIKTOK_RESEARCH_*`)
- `reddit_official` (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`)
- `hf_datasets`

### Opt-in

- `apify_tiktok`
- `apify_instagram`
- `apify_youtube`

All three require `APIFY_API_KEY`.

## Compliance note

Use connectors in line with platform Terms of Service and local legal/ethics requirements.  
See `tosmod/connectors/opt_in/LEGAL.md` for opt-in collector disclaimer.
