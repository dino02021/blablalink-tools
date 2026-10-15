"""Explicit mock-only aggregate status; read-only and no schema migration."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from adapters.local_system_status import read_system_status
from application.system_status_view import render_system_status


def main():
    parser = argparse.ArgumentParser(description="Read-only aggregate status for selected mock unions")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scope", action="append", required=True)
    args = parser.parse_args()
    try:
        status = read_system_status(args.workspace, tuple(args.scope))
        result = {"status": status, "view": asdict(render_system_status(status))}
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"mock status error: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
