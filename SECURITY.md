# Security policy

## Reporting vulnerabilities

Please **do not** open public GitHub issues for security-sensitive reports.

Email the maintainers privately with:

- Description of the issue
- Steps to reproduce
- Impact assessment

## Secrets

- Never commit `.env` or API keys
- Never paste live credentials in issues or PRs
- Rotate keys immediately if exposed

## Opt-in collectors

Scrapers under `tosmod/connectors/opt_in/` can violate platform ToS. Use only with explicit authorization and institutional ethics approval.
