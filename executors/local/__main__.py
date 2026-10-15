"""Usage: python -m executors.local demo --workspace <new temporary directory>."""

import argparse
import json
from pathlib import Path
import sqlite3
import zipfile

from adapters.local_backup import create_backup, restore_backup
from application.tasks import StateConflict
from executors.local.demo import run_demo, workspace_status
from executors.local.worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline mock Sheets migration; no external providers")
    parser.add_argument("command", choices=("demo", "status", "work", "backup", "restore"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--code-sha")
    parser.add_argument("--worker-id", default="offline-worker")
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--max-workflow-checks", type=int, default=10)
    parser.add_argument("--publish-mock", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--backup-id")
    args = parser.parse_args()
    try:
        if args.command in ("backup", "restore"):
            if not args.archive or not args.code_sha or args.command == "restore" and not args.backup_id:
                raise ValueError("backup/restore requires explicit archive, release and restore identity")
            result = (create_backup(args.workspace, args.archive, args.code_sha) if args.command == "backup" else
                restore_backup(args.archive, args.workspace, args.backup_id, args.code_sha))
        elif args.command == "work":
            if not args.code_sha:
                raise ValueError("work requires code SHA")
            result = run_worker(args.workspace, args.code_sha, args.worker_id, args.max_jobs, args.publish_mock,
                max_workflow_checks=args.max_workflow_checks)
        else:
            result = run_demo(args.workspace) if args.command == "demo" else workspace_status(args.workspace)
    except (ValueError, OSError, StateConflict, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(2, f"mock workspace error: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
