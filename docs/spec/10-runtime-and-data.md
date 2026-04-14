# Runtime And Data

## Data Categories

### 1. Repo Static Data

Stored under `data/`.

Important files:
- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`
- `nikke_checklist_name_codes.json`

Used for:
- Nikke lookup tables
- checklist preparation
- character detail normalization

### 2. Runtime Settings

Important runtime files:
- `accounts.json`
- `sessions.json`
- `service_account.json`
- `google_sheets_id.json`
- `union_account_map.json`

### 3. Generated Output

Output directories:
- `UnionRaid/`
- `CharacterDetails/`

### 4. Workflow Runtime Mapping

Files under `.github/runtime/` used by workflows and automation:
- `union_google_sheets_id.json`
- `nikke_google_sheets_id.json`
- `scheduler_config.json`

## Runtime File Contracts

### `google_sheets_id.json`

Core fields:
- `type`
- `sheet_name`
- `sheet_id`
- `union_id`

Routing rules:
- `type=UnionRaid` for union raid upload
- `type=CharacterDetails` for nikke upload and sync upload
- `union_id` is mandatory for routing

Example:

```json
[
  {
    "type": "UnionRaid",
    "sheets": [
      {
        "sheet_name": "Example Union Raid",
        "sheet_id": "example-union-raid-sheet",
        "union_id": "example-union-a"
      }
    ]
  },
  {
    "type": "CharacterDetails",
    "sheets": [
      {
        "sheet_name": "Example Character Details",
        "sheet_id": "example-character-sheet",
        "union_id": "example-union-a"
      }
    ]
  }
]
```

### `union_account_map.json`

Purpose:
- map `union_id -> username`
- define which accounts are included in `all` mode

Rules:
- `username` is the login account key
- pipelines de-duplicate by `username`
- `union_id` is still used for union raid routing

Example:

```json
[
  {
    "union_id": "example-union-a",
    "username": "example-account-a"
  },
  {
    "union_id": "example-union-b",
    "username": "example-account-b"
  }
]
```

### `scheduler_config.json`

Purpose:
- source of truth for cloud scheduling
- editable from GitHub UI or local git workflow
- consumed by the Cloudflare Worker scheduler

Core fields:
- `version`
- `timezone`
- `jobs`

Supported job types:
- `daily`
- `once`

`daily` fields:
- `name`
- `enabled`
- `type`
- `time`
- `workflow`
- `ref`
- `inputs`
- optional `days_of_week`

`once` fields:
- `name`
- `enabled`
- `type`
- `date`
- `time`
- `workflow`
- `ref`
- `inputs`

Example:

```json
{
  "version": 1,
  "timezone": "Asia/Taipei",
  "jobs": [
    {
      "name": "nikke_pipeline_daily",
      "enabled": true,
      "type": "daily",
      "time": "18:00",
      "workflow": "nikke_pipeline_run.yml",
      "ref": "main",
      "inputs": {
        "scope": "all"
      }
    },
    {
      "name": "union_raid_pipeline_once",
      "enabled": false,
      "type": "once",
      "date": "2026-12-31",
      "time": "18:10",
      "workflow": "union_raid_pipeline.yml",
      "ref": "main",
      "inputs": {
        "mode": "full",
        "scope": "all",
        "month": "2026/12"
      }
    }
  ]
}
```

## Generated Output Contracts

### Union Raid JSON

- upload routing uses the file content `union_id`
- file name is not the only routing source

### Character Details JSON

- one file per member
- file content must provide `nickname` and `character_details`
- upload may target only selected `files`

## Routing Rules

### Union Raid Upload

- route by `type=UnionRaid + union_id`

### Nikke Upload

- route by `type=CharacterDetails + union_id`

### Sync Upload

- route by `type=CharacterDetails + guild_id`
- `guild_id` is treated as the same routing domain as `union_id`

## Scheduling Rules

- cloud schedule definitions must live in `scheduler_config.json`
- GitHub workflow files should not be the primary source of production time schedules
- editing schedule behavior means editing both worker logic and spec if the contract changes

## Out Of Scope

This spec does not document:
- cloud account setup walkthroughs
- token creation walkthroughs
- manual operations checklists
