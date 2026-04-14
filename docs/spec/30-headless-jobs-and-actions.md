# Headless Jobs And Actions

## Job Layer

`lib/jobs/` is the shared execution layer for GUI, CLI, and automation.

Goals:
- convert GUI-capable flows into reusable headless jobs
- return stable `JobResult` objects
- keep routing and failure rules in Python instead of shell scripts

Shared base:
- [lib/jobs/common.py](../../lib/jobs/common.py)

## CLI Entry

CLI entry point:
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

Current commands:
- `union-raid-download`
- `union-raid-upload`
- `union-raid-pipeline`
- `nikke-batch-download`
- `nikke-pipeline`
- `nikke-upload`
- `sync-upload`

## Union Raid Jobs

### `run_union_raid_download`

File:
- [lib/jobs/union_raid_download.py](../../lib/jobs/union_raid_download.py)

Flow:
1. resolve account
2. load session
3. call `fetch_union_info()`
4. call `fetch_union_raid_data()`
5. write union raid JSON

### `run_union_raid_upload`

File:
- [lib/jobs/union_raid_upload.py](../../lib/jobs/union_raid_upload.py)

Flow:
1. load union raid JSON
2. extract `union_id`
3. resolve `type=UnionRaid + union_id`
4. fetch guild members
5. write Google Sheets

### `run_union_raid_pipeline`

File:
- [lib/jobs/union_raid_pipeline.py](../../lib/jobs/union_raid_pipeline.py)

Parameters:
- `mode=download|full`
- `scope=single|all`
- `month`

Rules:
- `single` runs one account
- `all` reads `BLABLALINK_UNION_ACCOUNT_MAP_JSON`, then falls back to local `union_account_map.json`
- `full` means download then upload
- this workflow is intended for manual dispatch and one-time external scheduling

## Nikke Jobs

### `run_nikke_batch_download`

File:
- [lib/jobs/nikke_batch_download.py](../../lib/jobs/nikke_batch_download.py)

Flow:
1. prepare checklist items
2. read `guild_id` and `nikke_area_id` from session context
3. fetch guild members
4. fetch characters and character details for each member
5. write `CharacterDetails/*.json`

### `run_nikke_upload`

File:
- [lib/jobs/nikke_upload.py](../../lib/jobs/nikke_upload.py)

Rules:
- route by `type=CharacterDetails + union_id`
- allow selected `files`
- fail when no matching sheet exists

### `run_nikke_pipeline`

File:
- [lib/jobs/nikke_pipeline.py](../../lib/jobs/nikke_pipeline.py)

Parameters:
- `scope=single|all`

Rules:
- `all` reads `BLABLALINK_UNION_ACCOUNT_MAP_JSON`, then falls back to local `union_account_map.json`
- de-duplicate by `username`
- skip accounts that have no matching `CharacterDetails` sheet
- fail the whole pipeline if any account truly fails

## Sync Upload

File:
- [lib/jobs/sync_upload.py](../../lib/jobs/sync_upload.py)

Parameters:
- `scope=single|all`

Rules:
- route by `guild_id -> CharacterDetails`
- `all` reads `BLABLALINK_UNION_ACCOUNT_MAP_JSON`, then falls back to local `union_account_map.json`
- de-duplicate by `username`

## GitHub Workflow Role

Workflow files remain execution wrappers for:
- manual dispatch from GitHub
- dispatch requests from the external scheduler

They should not be the production source of schedule timing.

Current workflow files:
- [.github/workflows/union_raid_pipeline.yml](../../.github/workflows/union_raid_pipeline.yml)
- [.github/workflows/nikke_pipeline_run.yml](../../.github/workflows/nikke_pipeline_run.yml)
- [.github/workflows/sync_upload.yml](../../.github/workflows/sync_upload.yml)

Current trigger model:
- `union_raid_pipeline`: manual dispatch only
- `nikke_pipeline`: manual dispatch plus external `workflow_dispatch`
- `sync_upload`: manual dispatch plus external `workflow_dispatch`

Each workflow now accepts optional scheduler metadata inputs:
- `scheduler_source`
- `scheduler_job_name`
- `scheduler_fire_time`

Those inputs are for observability only and do not change business logic.

## External Cloud Scheduler

Production scheduling is handled by:
- [cloudflare-scheduler/](../../cloudflare-scheduler/)

Components:
- [cloudflare-scheduler/wrangler.toml](../../cloudflare-scheduler/wrangler.toml)
- [cloudflare-scheduler/src/index.js](../../cloudflare-scheduler/src/index.js)
- [.github/runtime/scheduler_config.json](../../.github/runtime/scheduler_config.json)

Design:
- Cloudflare Cron Trigger runs every minute
- Worker loads scheduler config from the GitHub repository
- Worker evaluates due jobs in the configured timezone
- Worker dispatches GitHub workflows with `workflow_dispatch`

Why this exists:
- GitHub native `schedule` has proven unreliable on this repo
- schedules must be editable from GitHub or local git
- `union_raid_pipeline` needs one-time event-based scheduling after the event date is known

## External Scheduler Rules

- `scheduler_config.json` is the scheduling source of truth
- do not hard-code production schedule times in workflow YAML
- the worker must stay config-driven
- workflow file names are stable identifiers used by the scheduler
- one-time union raid jobs should be represented as `type=once` entries

## Workflow Development Rules

When changing workflows:
- keep shell logic thin
- move routing and validation into Python or worker code
- keep workflow inputs aligned with job parameters
- update spec whenever workflow behavior, scheduler contract, or dispatch semantics change

## Recommended Change Strategy

Change order:
1. `lib/jobs/*`
2. workflow inputs
3. cloud scheduler logic
4. scheduler config contract
5. spec
