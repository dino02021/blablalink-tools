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

## Secret mapping

Each secret is written back to the same file layout that the local app already uses.

- `BLABLALINK_SESSIONS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/sessions.json`
- `BLABLALINK_SHEETS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/google_sheets_id.json`
- `BLABLALINK_GOOGLE_CREDENTIALS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/service_account.json`
- `BLABLALINK_MEMBERS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/members_id.json`
- `BLABLALINK_NIKKES_INFO_JSON`
  Restored to `data/nikkes_info.json`
- `BLABLALINK_NIKKE_CHECKLIST_NAME_CODES_JSON`
  Restored to `data/nikke_checklist_name_codes.json`
- `BLABLALINK_DEFAULT_ACCOUNT`
  Default account slug used by workflows when `workflow_dispatch` does not override `account`

All JSON secrets must contain the full original file content, not base64 and not partial fragments.

## Manual workflow usage

### union_raid_pipeline

`workflow_dispatch` inputs:

- `mode`
  `download` or `full`
- `account`
  Optional. Overrides `BLABLALINK_DEFAULT_ACCOUNT`
- `month`
  Required when `mode=full`

Recommended first test:

1. Run `union_raid_pipeline`
2. Set `mode=download`
3. Leave `account` empty if `BLABLALINK_DEFAULT_ACCOUNT` is already correct
4. Confirm the download job succeeds
5. Run again with `mode=full` and a valid `month` such as `2026/04`

### nikke_pipeline

`workflow_dispatch` inputs:

- `mode`
  `download` or `full`
- `account`
  Optional. Overrides `BLABLALINK_DEFAULT_ACCOUNT`

Recommended first test:

1. Run `nikke_pipeline`
2. Set `mode=download`
3. Confirm the batch download job succeeds
4. Run again with `mode=full`

### sync_upload

`workflow_dispatch` inputs:

- `account`
  Optional. Overrides `BLABLALINK_DEFAULT_ACCOUNT`

Recommended first test:

1. Run `sync_upload`
2. Leave `account` empty if the default account is correct
3. Confirm the sync sheet upload completes without credential errors

## First-time GitHub setup checklist

1. Open the GitHub repo `Settings -> Secrets and variables -> Actions`
2. Create every secret listed in `GitHub Actions runtime secrets`
3. Verify the account slug in `BLABLALINK_DEFAULT_ACCOUNT` matches the key used in `sessions.json`
4. Open `Actions` and enable workflows if GitHub asks for confirmation
5. Run `union_raid_pipeline` in `download` mode first
6. Run `nikke_pipeline` in `download` mode first
7. Run `sync_upload`

## Notes

- Current workflows are manual-first. Cron schedule is not enabled yet.
- Secrets are reconstructed at runtime only. They are not committed back to the repository.
- Local GUI behavior is unchanged; the cloud workflows call the shared job layer instead of GUI code.
