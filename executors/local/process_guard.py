"""Owned child supervision with bounded time, output and native memory constraints."""

import hashlib
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from adapters.windows_child_job import WindowsChildJob


def run_python_module(module: str, arguments: tuple[str, ...], seconds=250, memory_limit_bytes=512*1024*1024,
                      output_limit_bytes=65536) -> dict:
    if (not isinstance(module, str) or len(module) > 128 or re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*)*", module) is None
            or not isinstance(arguments, tuple) or len(arguments) > 100 or any(not isinstance(arg, str) or "\x00" in arg for arg in arguments)
            or sum(len(arg.encode()) for arg in arguments) > 16384
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0.05 <= seconds <= 3600
            or type(memory_limit_bytes) is not int or not 32*1024*1024 <= memory_limit_bytes <= 2*1024**3
            or type(output_limit_bytes) is not int or not 1024 <= output_limit_bytes <= 1024*1024):
        raise ValueError("bounded trusted child invocation required")
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        raise ValueError("native child limits unavailable on this platform")
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL") if key in os.environ}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    fence = WindowsChildJob(memory_limit_bytes) if sys.platform == "win32" else None
    process = None
    started = time.monotonic()
    outcome = "completed"
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            command = (sys.executable, "-m", "executors.local.guarded_child", str(memory_limit_bytes), str(output_limit_bytes), module, *arguments)
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2], env=environment,
                stdin=subprocess.PIPE, stdout=output, stderr=errors, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32")
            if fence is not None:
                fence.attach(process)
            process.stdin.write(b"start\n")
            process.stdin.close()
            while True:
                code = process.poll()
                if any(os.fstat(stream.fileno()).st_size > output_limit_bytes for stream in (output, errors)):
                    outcome = "output_limit_terminated"
                    break
                if code is not None:
                    outcome = "completed" if code == 0 else "child_failed"
                    break
                if time.monotonic()-started >= seconds:
                    outcome = "deadline_terminated"
                    break
                try:
                    process.wait(timeout=min(0.05, max(0.001, seconds-(time.monotonic()-started))))
                except subprocess.TimeoutExpired:
                    pass
            _stop(process, fence)
            output.seek(0)
            errors.seek(0)
            stdout, stderr = output.read(output_limit_bytes+1), errors.read(output_limit_bytes+1)
            return {"outcome": outcome, "exit_code": process.returncode, "is_child_stopped": process.poll() is not None,
                "seconds": time.monotonic()-started, "memory_limit_bytes": memory_limit_bytes,
                "stdout": stdout[:output_limit_bytes].decode("utf-8", errors="replace"),
                "stderr_hash": hashlib.sha256(stderr).hexdigest(), "is_stderr_truncated": len(stderr) > output_limit_bytes}
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            _stop(process, fence)
        elif fence is not None:
            fence.close()


def _stop(process, fence):
    if fence is not None:
        fence.close()
    if process.poll() is None:
        if sys.platform == "win32":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=5)
