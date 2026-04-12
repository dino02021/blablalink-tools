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
