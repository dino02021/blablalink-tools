"""Usage: python -m executors.local demo --workspace <new temporary directory>."""

import argparse
import json
from pathlib import Path

from executors.local.demo import run_demo, workspace_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline mock Sheets migration; no external providers")
    parser.add_argument("command", choices=("demo", "status"))
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_demo(args.workspace) if args.command == "demo" else workspace_status(args.workspace)
    except (ValueError, OSError) as error:
        parser.exit(2, f"mock workspace error: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
