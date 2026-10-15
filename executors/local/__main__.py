"""Usage: python -m executors.local demo --workspace <new temporary directory>."""

import argparse
import json
from pathlib import Path

from executors.local.demo import run_demo, workspace_status
from executors.local.worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline mock Sheets migration; no external providers")
    parser.add_argument("command", choices=("demo", "status", "work"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--code-sha")
    parser.add_argument("--worker-id", default="offline-worker")
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--publish-mock", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "work":
            if not args.code_sha:
                raise ValueError("work requires code SHA")
            result = run_worker(args.workspace, args.code_sha, args.worker_id, args.max_jobs, args.publish_mock)
        else:
            result = run_demo(args.workspace) if args.command == "demo" else workspace_status(args.workspace)
    except (ValueError, OSError) as error:
        parser.exit(2, f"mock workspace error: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
