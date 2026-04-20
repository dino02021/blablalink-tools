# -*- coding: utf-8 -*-
"""Tests for union raid season support across jobs, GUI, and CLI."""

from __future__ import annotations

from lib.cli import run_job
from lib.gui import union_raid_update
from lib.jobs import common, union_raid_download, union_raid_pipeline


def test_download_union_async_switches_by_season(monkeypatch):
    calls = []

    async def fake_fetch_union_info(_ctx):
        return "guild-1", "intl-1", "area-1"

    async def fake_old(_ctx, union_id, intl_open_id, nikke_area_id):
        calls.append(("old", union_id, intl_open_id, nikke_area_id))
        return True

    async def fake_new(_ctx, area_id, guild_id, season_id):
        calls.append(("new", area_id, guild_id, season_id))
        return True

    monkeypatch.setattr(union_raid_download, "fetch_union_info", fake_fetch_union_info)
    monkeypatch.setattr(union_raid_download, "fetch_union_raid_data", fake_old)
    monkeypatch.setattr(union_raid_download, "fetch_union_raid_data_of_guild_season", fake_new)

    common.run_coro_sync(lambda: union_raid_download._download_union_async(object(), None))
    common.run_coro_sync(lambda: union_raid_download._download_union_async(object(), "99"))

    assert calls == [
        ("old", "guild-1", "intl-1", "area-1"),
        ("new", "area-1", "guild-1", "99"),
    ]


def test_run_union_raid_download_reports_season(monkeypatch):
    monkeypatch.setattr(union_raid_download, "resolve_account", lambda account: "tester")
    monkeypatch.setattr(union_raid_download, "load_session", lambda slug: {"slug": slug})
    monkeypatch.setattr(union_raid_download, "_find_latest_union_raid_file", lambda: None)
    monkeypatch.setattr(union_raid_download, "run_coro_sync", lambda fn: True)

    result = union_raid_download.run_union_raid_download(account="tester", season_id="12")

    assert result.ok is True
    assert result.stats["season_id"] == "12"


def test_union_raid_pipeline_passes_season_id(monkeypatch):
    captured = []

    monkeypatch.setattr(union_raid_pipeline, "_resolve_accounts", lambda scope, account: ["tester"])

    def fake_download(account=None, season_id=None):
        captured.append((account, season_id))
        return common.JobResult(ok=True, message="ok", outputs={"data_path": "tmp.json"})

    monkeypatch.setattr(union_raid_pipeline, "run_union_raid_download", fake_download)

    result = union_raid_pipeline.run_union_raid_pipeline(mode="download", scope="single", account="tester", season_id="55")

    assert result.ok is True
    assert result.stats["season_id"] == "55"
    assert captured == [("tester", "55")]


def test_start_union_raid_update_passes_season(monkeypatch):
    calls = []

    class FakeLabel:
        def configure(self, **_kwargs):
            return None

    class FakeRoot:
        @staticmethod
        def after(_delay, fn):
            fn()

    class FakeVar:
        @staticmethod
        def set(_value):
            return None

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    class FakeApp:
        def __init__(self):
            self._last_slug = "tester"
            self.root = FakeRoot()
            self.union_status_var = FakeVar()
            self.union_status_label = FakeLabel()

        def _set_status(self, _text, _color="green"):
            return None

        def _set_busy(self, _busy):
            return None

    monkeypatch.setattr(union_raid_update.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        union_raid_update,
        "run_union_raid_download",
        lambda account=None, season_id=None: calls.append(("download", account, season_id))
        or common.JobResult(ok=True, message="ok", outputs={"data_path": "tmp.json"}),
    )
    monkeypatch.setattr(
        union_raid_update,
        "run_union_raid_upload",
        lambda account=None, month=None, data_path=None: calls.append(("upload", account, month, data_path))
        or common.JobResult(ok=True, message="ok"),
    )

    union_raid_update.start_union_raid_update(FakeApp(), "single", "2026/04", "77")

    assert calls == [
        ("download", "tester", "77"),
        ("upload", "tester", "2026/04", "tmp.json"),
    ]


def test_cli_union_raid_download_accepts_season(monkeypatch, capsys):
    captured = {}

    def fake_download(account=None, season_id=None):
        captured["args"] = (account, season_id)
        return common.JobResult(ok=True, message="ok")

    monkeypatch.setattr(run_job, "run_union_raid_download", fake_download)

    exit_code = run_job.main(["union-raid-download", "--account", "tester", "--season", "123"])

    assert exit_code == 0
    assert captured["args"] == ("tester", "123")
    assert capsys.readouterr().out.strip() == "ok"


def test_cli_union_raid_pipeline_accepts_season(monkeypatch, capsys):
    captured = {}

    def fake_pipeline(mode="download", scope="single", account=None, month=None, season_id=None):
        captured["args"] = {
            "mode": mode,
            "scope": scope,
            "account": account,
            "month": month,
            "season_id": season_id,
        }
        return common.JobResult(ok=True, message="ok")

    monkeypatch.setattr(run_job, "run_union_raid_pipeline", fake_pipeline)

    exit_code = run_job.main(
        ["union-raid-pipeline", "--mode", "download", "--scope", "single", "--account", "tester", "--season", "222"]
    )

    assert exit_code == 0
    assert captured["args"]["season_id"] == "222"
    assert capsys.readouterr().out.strip() == "ok"
