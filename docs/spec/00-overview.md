# Overview

## Project Role

`BlaBlaLinkTool` is an internal Python toolchain for three workflow groups:
- BlaBlaLink login and session management
- Union Raid download and upload
- Nikke character detail and synchro upload

The same business logic must be reusable from:
- desktop GUI
- CLI
- GitHub Actions
- external cloud scheduler

## Architectural Rule

GUI is only responsible for interaction and status display.

Download, upload, routing, and batch orchestration should live in shared modules such as:
- `lib/jobs/`
- `lib/api.py`
- `lib/data_store.py`
- `lib/google_sheets/`

## Major Modules

### Entry Points

- [main.py](../../main.py)
- [main.pyw](../../main.pyw)
- [lib/gui_main.py](../../lib/gui_main.py)
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

### API And Session

- [lib/api.py](../../lib/api.py)
- [lib/auth.py](../../lib/auth.py)
- [lib/utils.py](../../lib/utils.py)

### Data And Runtime

- [lib/constants.py](../../lib/constants.py)
- [lib/data_store.py](../../lib/data_store.py)
- [lib/github_data_sync.py](../../lib/github_data_sync.py)
- [.github/runtime/](../../.github/runtime/)

### GUI

- [lib/gui/](../../lib/gui/)
- [lib/gui/dialogs/](../../lib/gui/dialogs/)

### Headless Jobs

- [lib/jobs/](../../lib/jobs/)

### Cloud Scheduler

- [cloudflare-scheduler/](../../cloudflare-scheduler/)

The external scheduler is now the production path for timed automation.
GitHub native `schedule` is not the source of truth for production timing on this repo.

## Supported Flows

### Union Raid

- download union raid JSON
- route upload by `union_id`
- support `single` and `all`
- support `download` and `full`
- allow one-time scheduled execution after the event date is known

### Nikke

- fetch guild members from the current session
- download character details for the whole guild
- route upload by `guild_id -> CharacterDetails`
- support `single` and `all`

### Sync Upload

- fetch `nickname -> synchro_level`
- route upload by `guild_id -> CharacterDetails`
- support `single` and `all`

## Source Of Truth Rules

- `union_id` and `guild_id` are routing keys
- `sheet_name` is display-only and not a routing key
- `account` selects session identity, not spreadsheet target
- cloud scheduling rules live in `.github/runtime/scheduler_config.json`
- workflow files remain manual dispatch entry points and execution wrappers

## Development Priorities

When behavior changes, preserve:
- shared logic across GUI, CLI, and automation
- stable `JobResult` shapes where practical
- routing rules concentrated in job layer
- config-driven scheduling instead of hard-coded cloud cron behavior
