# -*- coding: utf-8 -*-
import json
from urllib import error, request

from lib.gui.dialogs import scheduler_config


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_request_json_follows_github_redirect(monkeypatch):
    original_url = "https://api.github.com/repos/owner/repo/contents/.github/runtime/scheduler_config.json"
    redirected_url = "https://api.github.com/repositories/123/contents/.github/runtime/scheduler_config.json"
    seen: list[tuple[str, str]] = []

    def fake_urlopen(req: request.Request, timeout: int):
        assert timeout == scheduler_config.GITHUB_API_TIMEOUT_SECONDS
        seen.append((req.full_url, req.get_method()))
        if req.full_url == original_url:
            raise error.HTTPError(
                original_url,
                307,
                "Temporary Redirect",
                {"Location": redirected_url},
                None,
            )
        assert req.full_url == redirected_url
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(scheduler_config.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(scheduler_config, "_get_github_token", lambda: "")

    assert scheduler_config._request_json(original_url) == {"ok": True}
    assert seen == [(original_url, "GET"), (redirected_url, "GET")]
