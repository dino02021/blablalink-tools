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

## Healthcheck configuration

Nikke downloads, uploads, and the desktop healthcheck editor use the common Google
spreadsheet's `健檢名單` worksheet as the only role/trait selection source. The
service account needs access to both the common sheet and the union target sheets.
The default common spreadsheet ID is `167RcdRZu8OY5Im9wqWHDBUjZAiENJvv7mvFD74ZXzgU`;
`BLABLALINK_COMMON_CONFIG_SPREADSHEET_ID` can override it and must match Apps Script.
GitHub's `nikke_healthcheck_members.json` is no longer read or edited at runtime.

After changing the list, run the spreadsheet's healthcheck strict sync to update
role/trait columns. Python checks those headers before downloading and uploading.
Previously downloaded JSON files must be downloaded again to include the source
configuration and destination header fingerprints.

## GitHub Actions runtime secrets

Cloud workflows rebuild the existing local runtime files from GitHub Secrets.

Required secrets:

- `BLABLALINK_SESSIONS_JSON`
- `BLABLALINK_GOOGLE_CREDENTIALS_JSON`
- `BLABLALINK_UNION_ACCOUNT_MAP_JSON`
- `BLABLALINK_DEFAULT_ACCOUNT`

Available workflows:

- `union_raid_pipeline`
- `nikke_pipeline`
- `sync_upload`

## Secret mapping

Each secret is written back to the same file layout that the local app already uses.

- `BLABLALINK_SESSIONS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/sessions.json`
- `BLABLALINK_GOOGLE_CREDENTIALS_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/service_account.json`
- `BLABLALINK_UNION_ACCOUNT_MAP_JSON`
  Restored to `$HOME/Documents/BlablalinkToolSettings/union_account_map.json`
- `BLABLALINK_DEFAULT_ACCOUNT`
  Default account slug used by non-union workflows when `workflow_dispatch` does not override `account`

Workflow-managed sheet mappings now come from repo runtime files under `.github/runtime/`, not GitHub Secrets.

All JSON secrets must contain the full original file content, not partial fragments.

For `google_sheets_id.json`, `UnionRaid` and `CharacterDetails` entries can include `union_id`. When `union_raid_upload` does not receive an explicit `sheet_id`, it resolves the damage upload sheet from `type=UnionRaid + union_id`. After a successful upload, `union_raid_pipeline` resolves the raid planning spreadsheet from `type=CharacterDetails + union_id`.

For `union_account_map.json`, each item maps a `union_id` to the login account `username` that should download and upload that union's data. When `union_raid_pipeline` does not receive an explicit `account`, it runs download once for each unique mapped `username`, then upload resolves each downloaded file's `union_id` back to the mapped account.

Example:

```json
[
  {
    "type": "UnionRaid",
    "sheets": [
      {
        "sheet_name": "Main Union",
        "sheet_id": "your-damage-upload-sheet-id",
        "union_id": "your-union-id"
      }
    ]
  },
  {
    "type": "CharacterDetails",
    "sheets": [
      {
        "sheet_name": "Main Union",
        "sheet_id": "your-character-details-sheet-id",
        "union_id": "your-union-id"
      }
    ]
  }
]
```

`union_account_map.json` example:

```json
[
  {
    "union_id": "5010",
    "username": "account_a@gmail.com"
  },
  {
    "union_id": "3097",
    "username": "account_b@gmail.com"
  }
]
```

## Manual workflow usage

### union_raid_pipeline

`workflow_dispatch` inputs:

- `mode`
  `download` or `full`
- `account`
  Optional. If set, only that account runs. If empty, the workflow runs once for each unique `username` in `BLABLALINK_UNION_ACCOUNT_MAP_JSON`
- `month`
  Required when `mode=full`

Recommended first test:

1. Run `union_raid_pipeline`
2. Set `mode=download`
3. Leave `account` empty if `BLABLALINK_UNION_ACCOUNT_MAP_JSON` already contains the union-to-account mapping
4. Confirm the download job succeeds
5. Run again with `mode=full` and a valid `month` such as `2026/04`

### nikke_pipeline

`workflow_dispatch` inputs:

- `account`
  Optional. Overrides `BLABLALINK_DEFAULT_ACCOUNT`

Recommended first test:

1. Run `nikke_pipeline`
2. Leave `account` empty if the default account is correct
3. Confirm the combined download + upload job succeeds

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
3. Verify every `username` used in `BLABLALINK_UNION_ACCOUNT_MAP_JSON` exists in `sessions.json`
4. Open `Actions` and enable workflows if GitHub asks for confirmation
5. Run `union_raid_pipeline` in `download` mode first
6. Run `nikke_pipeline`
7. Run `sync_upload`

## Notes

- Current workflows are manual-first. Cron schedule is not enabled yet.
- Secrets are reconstructed at runtime only. Sheet mapping runtime files are now stored in the repository under `.github/runtime/`.
- Local GUI behavior is unchanged; the cloud workflows call the shared job layer instead of GUI code.
