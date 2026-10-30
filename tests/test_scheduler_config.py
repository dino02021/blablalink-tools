# -*- coding: utf-8 -*-
import base64
import json
from types import SimpleNamespace
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


def test_fetch_scheduler_config_reads_main_even_on_feature_branch(monkeypatch):
    payload = {"jobs": [{"name": "union_raid_pipeline_once", "inputs": {"month": "2026/11"}}]}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    seen: list[str] = []

    monkeypatch.setattr(scheduler_config, "_get_git_remote", lambda: "owner/repo")
    monkeypatch.setattr(
        scheduler_config.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="local/mock-sheets-migration\n", returncode=0),
    )

    def fake_request_json(url: str, *args, **kwargs):
        seen.append(url)
        return {"content": encoded}

    monkeypatch.setattr(scheduler_config, "_request_json", fake_request_json)

    assert scheduler_config._fetch_scheduler_config_from_github() == payload
    assert seen == [
        "https://api.github.com/repos/owner/repo/contents/.github/runtime/scheduler_config.json?ref=main"
    ]


def test_push_scheduler_config_targets_main_even_on_feature_branch(monkeypatch):
    calls: list[tuple[str, str, object, bool]] = []

    monkeypatch.setattr(scheduler_config, "_get_git_remote", lambda: "owner/repo")
    monkeypatch.setattr(
        scheduler_config.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="local/mock-sheets-migration\n", returncode=0),
    )

    def fake_request_json(url: str, method: str = "GET", body=None, auth_required: bool = False):
        calls.append((url, method, body, auth_required))
        if method == "GET":
            return {"sha": "old-sha"}
        return {}

    monkeypatch.setattr(scheduler_config, "_request_json", fake_request_json)

    assert scheduler_config._push_to_github(".github/runtime/scheduler_config.json", "{}")
    assert calls[0][0].endswith(".github/runtime/scheduler_config.json?ref=main")
    assert calls[1][2]["branch"] == "main"
