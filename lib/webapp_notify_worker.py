# -*- coding: utf-8 -*-
"""背景子程序：讀取 payload 檔後通知 Apps Script Web App。"""

from __future__ import annotations

import datetime
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from .log_redaction import redact_sensitive_data
except ImportError:
    from log_redaction import redact_sensitive_data


def _log(message: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [webapp_notify_worker] {redact_sensitive_data(message)}"
    try:
        project_root = Path(__file__).resolve().parent.parent
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "blablalinkTool.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _load_payload(payload_path: Path) -> tuple[str, dict]:
    raw = json.loads(payload_path.read_text(encoding="utf-8"))
    url = str(raw.get("url") or "").strip()
    payload = raw.get("payload") or {}
    kind = str(raw.get("kind") or "").strip()
    if kind:
        payload["kind"] = kind
    return url, payload


def main() -> int:
    if len(sys.argv) < 2:
        _log("missing payload path")
        return 1

    payload_path = Path(sys.argv[1]).resolve()
    if not payload_path.exists():
        _log(f"payload not found: {payload_path}")
        return 1

    try:
        url, payload = _load_payload(payload_path)
        if not url:
            _log("missing web app url")
            return 1
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            _log(f"status={status} body={body}")
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        _log(f"http_error status={exc.code} body={body}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _log(f"exception: {exc}")
        return 1
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
