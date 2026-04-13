# -*- coding: utf-8 -*-
"""CLI entrypoint for headless jobs."""

from __future__ import annotations

import argparse
import json
from typing import Callable

from ..github_data_sync import flush_cloud_data_sync, pull_cloud_data_files
from ..jobs.common import JobResult
from ..jobs.nikke_batch_download import run_nikke_batch_download
from ..jobs.nikke_pipeline import run_nikke_pipeline
from ..jobs.nikke_upload import run_nikke_upload
from ..jobs.sync_upload import run_sync_upload
from ..jobs.union_raid_download import run_union_raid_download
from ..jobs.union_raid_pipeline import run_union_raid_pipeline
from ..jobs.union_raid_upload import run_union_raid_upload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BlaBlaLink headless jobs.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print JSON result.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_union_download = sub.add_parser("union-raid-download", help="Download union raid data.")
    p_union_download.add_argument("--account", default="", help="Account slug.")

    p_union_upload = sub.add_parser("union-raid-upload", help="Upload union raid data.")
    p_union_upload.add_argument("--account", default="", help="Account slug.")
    p_union_upload.add_argument("--month", required=True, help="Month text, e.g. 2026/04.")
    p_union_upload.add_argument("--data-file", default="", help="Union raid data file.")
    p_union_upload.add_argument("--sheet-id", default="", help="Override sheet id.")

    p_union_pipeline = sub.add_parser("union-raid-pipeline", help="Run union raid pipeline.")
    p_union_pipeline.add_argument("--mode", default="download", help="download or full.")
    p_union_pipeline.add_argument("--scope", default="single", help="single or all.")
    p_union_pipeline.add_argument("--account", default="", help="Account slug for single scope.")
    p_union_pipeline.add_argument("--month", default="", help="Month text for full mode.")

    p_nikke_batch = sub.add_parser("nikke-batch-download", help="Batch download Nikke data.")
    p_nikke_batch.add_argument("--account", default="", help="Account slug.")

    p_nikke_pipeline = sub.add_parser("nikke-pipeline", help="Run Nikke download and upload pipeline.")
    p_nikke_pipeline.add_argument("--account", default="", help="Account slug.")

    p_nikke_upload = sub.add_parser("nikke-upload", help="Upload Nikke data.")
    p_nikke_upload.add_argument("--account", default="", help="Account slug.")
    p_nikke_upload.add_argument("--files-json", default="", help="JSON array of filenames to upload.")
    p_nikke_upload.add_argument("--input-dir", default="", help="Override character details directory.")
    p_nikke_upload.add_argument("--sheet-id", default="", help="Override sheet id.")

    p_sync = sub.add_parser("sync-upload", help="Upload synchro levels.")
    p_sync.add_argument("--account", default="", help="Account slug.")
    p_sync.add_argument("--worksheet", default="成員名稱", help="Worksheet name.")
    return parser


def _emit_result(result: JobResult, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "message": result.message,
                    "stats": result.stats,
                    "outputs": result.outputs,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(result.message)
        if result.stats:
            print(f"stats={json.dumps(result.stats, ensure_ascii=False)}")
        if result.outputs:
            print(f"outputs={json.dumps(result.outputs, ensure_ascii=False)}")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    pull_cloud_data_files()

    fn: Callable[[], JobResult]
    if args.command == "union-raid-download":
        fn = lambda: run_union_raid_download(account=args.account)
    elif args.command == "union-raid-upload":
        fn = lambda: run_union_raid_upload(
            account=args.account,
            month=args.month,
            data_path=args.data_file or None,
            sheet_id=args.sheet_id or None,
        )
    elif args.command == "union-raid-pipeline":
        fn = lambda: run_union_raid_pipeline(
            mode=args.mode,
            scope=args.scope,
            account=args.account or None,
            month=args.month or None,
        )
    elif args.command == "nikke-batch-download":
        fn = lambda: run_nikke_batch_download(account=args.account)
    elif args.command == "nikke-pipeline":
        fn = lambda: run_nikke_pipeline(account=args.account)
    elif args.command == "nikke-upload":
        files = None
        if args.files_json:
            try:
                raw_files = json.loads(args.files_json)
            except Exception as exc:
                parser.error(f"Invalid --files-json: {exc}")
                return 2
            if not isinstance(raw_files, list):
                parser.error("--files-json must be a JSON array")
                return 2
            files = [str(item) for item in raw_files]
        fn = lambda: run_nikke_upload(
            account=args.account or None,
            files=files,
            input_dir=args.input_dir or None,
            sheet_id=args.sheet_id or None,
        )
    elif args.command == "sync-upload":
        fn = lambda: run_sync_upload(account=args.account, worksheet_name=args.worksheet)
    else:
        parser.error(f"Unknown command: {args.command}")
        return 2

    try:
        result = fn()
    finally:
        flush_cloud_data_sync()
    return _emit_result(result, args.as_json)


if __name__ == "__main__":
    raise SystemExit(main())
