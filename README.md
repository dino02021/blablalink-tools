# BlaBlaLinkTool

Desktop tool for BlaBlaLink / Nikke workflows.

## Repository policy

- Source code, tests, and docs are tracked in git.
- Generated data, logs, caches, and local credentials are not tracked.

## Not included in this repository

The following runtime data is intentionally excluded:

- `data/`
- local session files
- Google Sheets credentials
- other machine-local settings

These files must be prepared locally before running features that depend on them.

## GitHub Actions runtime secrets

Cloud workflows rebuild the existing local runtime files from GitHub Secrets.

Required secrets:

- `BLABLALINK_SESSIONS_JSON`
- `BLABLALINK_SHEETS_JSON`
- `BLABLALINK_GOOGLE_CREDENTIALS_JSON`
- `BLABLALINK_MEMBERS_JSON`
- `BLABLALINK_NIKKES_INFO_JSON`
- `BLABLALINK_NIKKE_CHECKLIST_NAME_CODES_JSON`
- `BLABLALINK_DEFAULT_ACCOUNT`

Available workflows:

- `union_raid_pipeline`
- `nikke_pipeline`
- `sync_upload`
