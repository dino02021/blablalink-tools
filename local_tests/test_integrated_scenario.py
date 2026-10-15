import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.local_system_status import read_system_status
from adapters.mock_sheets import MockSheets
from executors.local.scenario import SCOPE, run_scenario


def test_whole_scenario_preserves_sources_human_edits_and_fenced_recovery(tmp_path):
    directory = tmp_path / "system"
    result = run_scenario(directory)
    assert result["network_providers"] == [] and result["is_system_settled"]
    assert result["collection_attempts"] == 2 and result["collector_calls"] == result["collector_calls_after_standard_edit"] == 12
    assert len(result["compute_invocations"]) == 3
    assert all(run["outcome"] == "completed" and run["is_child_stopped"] and run["memory_limit_bytes"] == 512*1024*1024 for run in result["compute_invocations"])
    assert result["first_config_id"] != result["final_config_id"]
    book = MockSheets(directory / "workbooks").read("output_scenario")
    assert book["generated"]["config_id"] == result["final_config_id"]
    assert book["generated"]["snapshot_id"] == result["training_snapshot_id"]
    assert book["generated"]["standards"]["rules"][0]["metrics"][0]["threshold"] == 9
    scores = {row["member_id"]: (row["value"], row["rank"]) for row in book["generated"]["scores"]["groups"]}
    assert sorted(scores.values()) == [(40, 2), (100, 1)]
    assert len(book["generated_grid"]["tabs"]) == 8
    assert book["manual_tabs"]["manual"]["cells"][0][0]["value"] == {"stringValue": "collaborator changed"}
    assert result["reports"]["message_count"] == result["reports"]["delivery_count"] == 2
    assert result["raid"]["source_healthcheck_result_id"] and result["raid"]["confirmation_id"]
    restored = read_system_status(directory / "restored", (SCOPE,))
    assert restored["is_restore_fenced"]
    assert json.loads(json.dumps(restored["scopes"][0])) == result["scope_status"]
    assert result["retention_deleted_count"] >= 1
    assert json.loads((directory / "scenario-result.json").read_text(encoding="utf-8")) == result


def test_independent_scenario_runs_keep_identical_data_and_calculation_versions(tmp_path):
    first, second = (run_scenario(tmp_path / name) for name in ("one", "two"))
    for key in ("reference_id", "reference_version", "raw_snapshot_id", "training_snapshot_id", "first_config_id", "final_config_id", "generated_view_id"):
        assert first[key] == second[key]
    assert first["raid"] == second["raid"]
    assert first["first_job_id"] == second["first_job_id"]  # same semantic job fingerprint
    assert first["scope_status"]["last_published"]["publication_id"] != second["scope_status"]["last_published"]["publication_id"]


def test_integrated_scenario_refuses_even_empty_preexisting_workspace(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    with pytest.raises(ValueError, match="new workspace"):
        run_scenario(directory)
    assert list(directory.iterdir()) == []


def test_real_scenario_cli_uses_only_synthetic_mock_workspace(tmp_path):
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, "-m", "executors.local.scenario", "--workspace", str(tmp_path / "cli")],
        cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, timeout=30, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    report = json.loads(result.stdout)
    assert report["is_mock"] and report["network_providers"] == [] and report["is_restored_fenced"]
