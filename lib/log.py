# -*- coding: utf-8 -*-
"""
簡單的紀錄工具：統一寫入 logs/blablalinkTool.log 並同步印出。
"""
import datetime
import inspect
import traceback

from .constants import LOG_PATH, LOG_DIR
from .log_redaction import redact_sensitive_data


def log(msg: str) -> None:
    """寫入日誌並印出，若發生例外則安靜忽略。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {redact_sensitive_data(msg)}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        try:
            safe = line.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
            print(safe)
        except Exception:
            pass


def _resolve_func_name() -> str:
    """自動取得呼叫端的函式名。"""
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    try:
        return caller.f_code.co_name if caller else "unknown"
    finally:
        del frame
        del caller


def log_exc(exc: Exception) -> None:
    """統一的例外紀錄格式：[呼叫端] exception: <exc>。"""
    name = _resolve_func_name()
    detail = str(exc).strip() or repr(exc)
    exc_type = type(exc).__name__
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    message = f"[{name}] exception: {detail} (type={exc_type})"
    if trace:
        message += f"\n{trace}"
    log(message)


def log_msg(msg: str) -> None:
    """一般訊息格式：[呼叫端] message: <msg>。"""
    name = _resolve_func_name()
    log(f"[{name}] message: {msg}")
