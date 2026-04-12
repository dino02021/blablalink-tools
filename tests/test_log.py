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
