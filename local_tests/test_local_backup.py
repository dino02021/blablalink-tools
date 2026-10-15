import zipfile
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.local_backup import create_backup, restore_backup
from adapters.local_outbox import LocalOutbox
from adapters.local_state import LocalStateStore
from adapters.mock_discord import MockDiscord
from adapters.mock_sheets import MockSheets
from application.delivery import DeliverySpec, send_next
from application.tasks import StateConflict, TaskSpec
from executors.local.demo import run_demo, workspace_status
from executors.local.worker import run_worker


def test_quiescent_backup_restores_exact_data_with_worker_fence(tmp_path):
    source = tmp_path / "source"
    summary = run_demo(source)
    outbox = LocalOutbox(source / "state.sqlite")
    provider = MockDiscord(source / "messages.sqlite")
    spec = DeliverySpec("intent", "report", "member", "route", "thread", ("mock report",))
    outbox.enqueue("request", spec, spec.outbox_id)
    send_next(outbox, provider, spec.outbox_id, "worker")
    outbox.close()
    provider.close()
    archive = tmp_path / "backup.zip"
    backup = create_backup(source, archive, "mock-release-v1")
    restored = tmp_path / "restored"
    result = restore_backup(archive, restored, backup["backup_id"], "mock-release-v1")
    assert result["execution_mode"] == "restored_read_only"
    assert workspace_status(restored)["jobs"] == workspace_status(source)["jobs"]
    assert MockSheets(restored / "workbooks").read("output_demo") == MockSheets(source / "workbooks").read("output_demo")
    with pytest.raises(ValueError, match="fenced"):
        run_worker(restored, "mock-release-v1", is_publish_enabled=True)
    state = LocalStateStore(restored / "state.sqlite")
    recovered_outbox = LocalOutbox(restored / "state.sqlite")
    messages = MockDiscord(restored / "messages.sqlite")
    try:
        assert state.get_job(summary["job_id"]).state == "succeeded"
        assert recovered_outbox.parts(spec.outbox_id)[0]["state"] == "sent"
        assert len(messages.messages()) == 1
    finally:
        state.close()
        recovered_outbox.close()
        messages.close()


def test_active_compute_and_uncertain_delivery_cannot_be_backed_up(tmp_path):
    source = tmp_path / "source"
    run_demo(source)
    store = LocalStateStore(source / "state.sqlite")
    try:
        job = store.submit_job("new", TaskSpec("prepare_layout", "scope", "code"))
        store.claim("worker", "code")
        with pytest.raises(StateConflict):
            create_backup(source, tmp_path / "blocked.zip", "code")
        assert not (tmp_path / "blocked.zip").exists()
        store.cancel(job.job_id)
    finally:
        store.close()
    outbox = LocalOutbox(source / "state.sqlite")
    spec = DeliverySpec("intent", "report", "member", "route", "thread", ("mock",))
    try:
        outbox.enqueue("send", spec, spec.outbox_id)
        claim = outbox.claim(spec.outbox_id, "sender")
        outbox.uncertain(claim)
        with pytest.raises(StateConflict):
            create_backup(source, tmp_path / "blocked.zip", "code")
    finally:
        outbox.close()


@pytest.mark.parametrize("change", ["identity", "release", "content", "path"])
def test_restore_rejects_wrong_identity_release_content_and_paths(tmp_path, change):
    source = tmp_path / "source"
    run_demo(source)
    archive = tmp_path / "backup.zip"
    backup = create_backup(source, archive, "mock-release-v1")
    selected = archive
    if change in ("content", "path"):
        selected = tmp_path / "changed.zip"
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(selected, "w") as output:
            for entry in original.infolist():
                content = original.read(entry.filename)
                output.writestr(entry.filename, b"changed" if change == "content" and entry.filename == "state.sqlite" else content)
            if change == "path":
                output.writestr("../escape.txt", "unsafe")
    restored = tmp_path / "restored"
    with pytest.raises((ValueError, StateConflict)):
        restore_backup(selected, restored, "0" * 64 if change == "identity" else backup["backup_id"],
            "wrong-release" if change == "release" else "mock-release-v1")
    assert not restored.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_backup_and_restore_never_overwrite_existing_paths(tmp_path):
    source = tmp_path / "source"
    run_demo(source)
    archive = tmp_path / "backup.zip"
    backup = create_backup(source, archive, "code")
    before = archive.read_bytes()
    with pytest.raises(ValueError):
        create_backup(source, archive, "code")
    with pytest.raises(ValueError):
        restore_backup(archive, source, backup["backup_id"], "code")
    assert archive.read_bytes() == before


def test_actual_backup_restore_cli_leaves_clone_fenced(tmp_path):
    source, restored = tmp_path / "source", tmp_path / "restored"
    run_demo(source)
    archive = tmp_path / "backup.zip"
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    root = Path(__file__).resolve().parents[1]
    base = [sys.executable, "-m", "executors.local"]
    backup = subprocess.run(base + ["backup", "--workspace", str(source), "--archive", str(archive), "--code-sha", "mock-release-v1"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
    backup_id = json.loads(backup.stdout)["backup_id"]
    restore = subprocess.run(base + ["restore", "--workspace", str(restored), "--archive", str(archive),
        "--backup-id", backup_id, "--code-sha", "mock-release-v1"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10, check=True)
    assert json.loads(restore.stdout)["execution_mode"] == "restored_read_only"
    blocked = subprocess.run(base + ["work", "--workspace", str(restored), "--code-sha", "mock-release-v1"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert blocked.returncode == 2
