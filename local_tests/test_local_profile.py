import pytest

from executors.local.profile import percentile, profile_local


def test_small_profile_is_bounded_synthetic_and_deterministic():
    report = profile_local(repeats=2, is_small=True)
    assert report["is_mock"] and report["members"] == 3 and report["roles"] == 5
    assert all(case["is_deterministic"] and len(case["samples"]) == 2 for case in report["measurements"])
    assert all(sample["checks"] > 0 and sample["sampled_peak_rss_bytes"] > 0 for case in report["measurements"] for sample in case["samples"])


def test_profile_bounds_and_nearest_rank_percentiles():
    for repeats in (0, True, 6):
        with pytest.raises(ValueError):
            profile_local(repeats=repeats)
    assert percentile([3, 1, 2], .5) == 2
    assert percentile([3, 1, 2], .95) == 3
