"""Compute-only mock worker in an owned, resource-bounded child process."""

import argparse
import json
from pathlib import Path
import sqlite3

from application.tasks import StateConflict, validate_identifier
from executors.local.demo import workspace_status
from executors.local.process_guard import run_python_module


def run_isolated_worker(directory, code_sha, worker_id="isolated-worker", max_jobs=1, seconds=250, memory_limit_bytes=512*1024*1024):
    directory = Path(directory).resolve()
    validate_identifier(code_sha)
    validate_identifier(worker_id)
    if type(max_jobs) is not int or not 1 <= max_jobs <= 100:
        raise ValueError("bounded isolated worker job count required")
    workspace_status(directory)
    if (directory / "restore-lock.json").exists():
        raise ValueError("restored workspace is fenced read-only")
    receipt = run_python_module("executors.local", ("work", "--workspace", str(directory), "--code-sha", code_sha,
        "--worker-id", worker_id, "--max-jobs", str(max_jobs)), seconds, memory_limit_bytes)
    raw = receipt.pop("stdout")
    result = None
    if receipt["outcome"] == "completed":
        try:
            result = json.loads(raw)
            if result.get("is_mock") is not True or result.get("network_providers") != [] or not isinstance(result.get("processed"), list):
                raise ValueError("invalid mock child result")
        except (ValueError, AttributeError):
            receipt["outcome"] = "child_protocol_invalid"
            result = None
    return {"is_mock": True, "is_isolated": True, **receipt, "child_result": result, "network_providers": []}


def main():
    parser = argparse.ArgumentParser(description="Compute-only mock worker with native child limits")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--worker-id", default="isolated-worker")
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--seconds", type=float, default=250)
    parser.add_argument("--memory-mib", type=int, default=512)
    args = parser.parse_args()
    try:
        result = run_isolated_worker(args.workspace, args.code_sha, args.worker_id, args.max_jobs, args.seconds, args.memory_mib*1024*1024)
    except (ValueError, OSError, StateConflict, sqlite3.Error) as error:
        parser.exit(2, f"isolated mock workspace error: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["outcome"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
