"""Tests for synchronized prospective validation (scripts/prospective_validation.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prospective_validation as pv


def test_failed_fetch_is_never_skip():
    decision, reason = pv.classify_prediction(
        any_ok=False, live_count=0, baseline_count=100,
        live_fp=None, baseline_fp="abc")
    assert decision == "unknown"
    assert "no_live" in reason


def test_no_live_features_is_unknown_not_skip():
    decision, _ = pv.classify_prediction(
        any_ok=True, live_count=0, baseline_count=100,
        live_fp=None, baseline_fp="abc")
    assert decision == "failed"


def test_incomplete_coverage_is_unknown():
    decision, reason = pv.classify_prediction(
        any_ok=True, live_count=50, baseline_count=100,
        live_fp="xyz", baseline_fp="abc")
    assert decision == "unknown"
    assert "incomplete_coverage" in reason


def test_fingerprint_match_is_unchanged():
    decision, _ = pv.classify_prediction(
        any_ok=True, live_count=100, baseline_count=100,
        live_fp="same", baseline_fp="same")
    assert decision == "unchanged"


def test_fingerprint_mismatch_is_changed():
    decision, _ = pv.classify_prediction(
        any_ok=True, live_count=100, baseline_count=100,
        live_fp="new", baseline_fp="old")
    assert decision == "changed"


def test_classify_result_matrix():
    assert pv.classify_result("unchanged", "unchanged") == "correct_skip"
    assert pv.classify_result("unchanged", "changed") == "missed_change"
    assert pv.classify_result("changed", "changed") == "correct_detection"
    assert pv.classify_result("changed", "unchanged") == "false_alarm"
    assert pv.classify_result("unknown", "changed") == "fallback_required"
    assert pv.classify_result("failed", "unchanged") == "fallback_required"
    assert pv.classify_result("unchanged", "inconclusive") == "inconclusive"


def test_unknown_never_counts_as_correct_skip():
    # Regression: an unknown/failed prediction must never be scored as a skip.
    assert pv.classify_result("unknown", "unchanged") != "correct_skip"
    assert pv.classify_result("failed", "unchanged") != "correct_skip"


def test_load_cohort_only_comparable(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(
        "spider,actual_status\n"
        "a,unchanged\nb,changed\nc,inconclusive\n"
    )
    cohort = pv.load_cohort(p)
    assert set(cohort) == {"a", "b"}
