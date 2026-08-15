# -*- coding: utf-8 -*-
"""針對 lib.log 的基本單元測試。"""

from pathlib import Path

from lib import log


def test_log_msg_and_exc(tmp_path, monkeypatch):
    log_file: Path = tmp_path / "test.log"
    monkeypatch.setattr(log, "LOG_PATH", log_file)
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)

    log.log_msg("hello")
    log.log_exc(Exception("boom"))

    content = log_file.read_text(encoding="utf-8")
    assert "message: hello" in content
    assert "exception: boom" in content
    assert "type=Exception" in content
    assert "[test_log_msg_and_exc]" in content


def test_log_exc_keeps_blank_exception_type_and_traceback(tmp_path, monkeypatch):
    log_file: Path = tmp_path / "test.log"
    monkeypatch.setattr(log, "LOG_PATH", log_file)
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)

    try:
        raise PermissionError()
    except PermissionError as exc:
        log.log_exc(exc)

    content = log_file.read_text(encoding="utf-8")
    assert "exception: PermissionError() (type=PermissionError)" in content
    assert "Traceback (most recent call last)" in content
    assert "test_log_exc_keeps_blank_exception_type_and_traceback" in content


def test_log_redacts_account_password_and_session_values(tmp_path, monkeypatch):
    log_file: Path = tmp_path / "test.log"
    monkeypatch.setattr(log, "LOG_PATH", log_file)
    monkeypatch.setattr(log, "LOG_DIR", tmp_path)

    log.log(
        "accounts=[{'username': 'user@example.com', 'password': 'plain-secret'}] "
        "account=other@example.com token=token-value cookie=session-value"
    )

    content = log_file.read_text(encoding="utf-8")
    assert "user@example.com" not in content
    assert "other@example.com" not in content
    assert "plain-secret" not in content
    assert "token-value" not in content
    assert "session-value" not in content
    assert content.count("<redacted>") >= 5
