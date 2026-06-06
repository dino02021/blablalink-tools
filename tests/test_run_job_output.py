# -*- coding: utf-8 -*-

from lib.cli.run_job import _emit_result
from lib.jobs.common import JobResult


def test_emit_summary_only_hides_stats_and_outputs(capsys):
    result = JobResult(
        ok=True,
        message="上傳妮姬資料成功 (31/32)\n月下 (3097): 31/32",
        stats={"ok_count": 31, "total_count": 32},
        outputs={"_summary_only": True, "failures": [], "unions": [{"union_id": "3097"}]},
    )

    code = _emit_result(result, as_json=False)

    assert code == 0
    assert capsys.readouterr().out == "上傳妮姬資料成功 (31/32)\n月下 (3097): 31/32\n"


def test_emit_json_hides_private_outputs(capsys):
    result = JobResult(
        ok=False,
        message="failed",
        stats={"failed_count": 1},
        outputs={"_summary_only": True, "failures": [{"account": "a"}]},
    )

    code = _emit_result(result, as_json=True)

    assert code == 1
    assert '"_summary_only"' not in capsys.readouterr().out
