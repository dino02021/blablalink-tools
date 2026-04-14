# Cloudflare Scheduler

This worker replaces GitHub native `schedule` for automation.

It runs every minute, reads the repository scheduler config, and dispatches GitHub workflows with `workflow_dispatch`.

## Runtime Inputs

Configured in [wrangler.toml](./wrangler.toml):
- `GITHUB_OWNER`
- `GITHUB_REPO`
- `GITHUB_REF`
- `CONFIG_PATH`
- `CONFIG_REF`

Secret required:
- `GITHUB_TOKEN`

## Token Requirements

Use one of:
- Classic PAT with `repo` and `workflow`
- Fine-grained PAT with:
  - `Contents: Read`
  - `Actions: Write`

## Deploy

1. Install Wrangler.
2. Authenticate to Cloudflare.
3. Set the GitHub token secret:

```bash
wrangler secret put GITHUB_TOKEN
```

4. Deploy:

```bash
wrangler deploy
```

## Schedule Configuration

The worker reads:
- [../.github/runtime/scheduler_config.json](../.github/runtime/scheduler_config.json)

This file is the source of truth for cloud scheduling.

You can edit it:
- locally, then push
- directly on GitHub

## Job Types

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

## Manual Preview

After deploy, open the worker URL in a browser.

It returns:
- current UTC time
- current zoned time
- due jobs
- config validation errors

It does not dispatch jobs on HTTP GET.
