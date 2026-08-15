from lib import webapp_notify_worker


def test_worker_log_redacts_sensitive_values(tmp_path, monkeypatch):
    fake_module = tmp_path / "lib" / "webapp_notify_worker.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(webapp_notify_worker, "__file__", str(fake_module))

    webapp_notify_worker._log(
        "username=user@example.com password=plain-secret cookie=session-value"
    )

    content = (tmp_path / "logs" / "blablalinkTool.log").read_text(encoding="utf-8")
    assert "user@example.com" not in content
    assert "plain-secret" not in content
    assert "session-value" not in content
    assert content.count("<redacted>") == 3
