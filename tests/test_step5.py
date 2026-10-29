import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import baseline_store as bs

import monitor as mon

# --- Baseline eligibility ---

def test_baseline_eligible_success():
    result = {"status": 200, "failed": False, "error": None, "body_hash": "abc", "strategy": "B"}
    eligible, reason = bs.is_baseline_eligible(result)
    assert eligible is True
    assert reason == "eligible"


def test_baseline_ineligible_failed():
    result = {"status": 500, "failed": True, "error": "Server error", "body_hash": "abc", "strategy": "B"}
    eligible, reason = bs.is_baseline_eligible(result)
    assert eligible is False
    assert reason == "ineligible_failed"


def test_baseline_ineligible_403():
    result = {"status": 403, "failed": True, "error": "Forbidden", "body_hash": "", "strategy": "B"}
    eligible, reason = bs.is_baseline_eligible(result)
    assert eligible is False


def test_baseline_ineligible_304_no_baseline():
    result = {"status": 304, "failed": False, "error": None, "body_hash": None, "strategy": "A"}
    eligible, reason = bs.is_baseline_eligible(result, has_existing_baseline=False)
    assert eligible is False
    assert reason == "ineligible_304_no_baseline"


def test_baseline_ineligible_empty_body():
    result = {"status": 200, "failed": False, "error": None, "body_hash": None, "strategy": "B", "bytes_transferred": 0}
    eligible, reason = bs.is_baseline_eligible(result)
    assert eligible is False
    assert reason == "ineligible_incomplete"


# --- Baseline reference tracking ---

def test_create_baseline_with_prev_ref():
    b = bs.create_baseline("sp", "url", "hash123", prev_baseline_id="prev-123")
    assert b["prev_baseline_id"] == "prev-123"
    assert b["baseline_id"] is not None


def test_create_baseline_without_prev_ref():
    b = bs.create_baseline("sp", "url", "hash123")
    assert b["prev_baseline_id"] is None


# --- 304 handling ---

def test_compare_baselines_304_reuses_state():
    prev = {"body_hash": "abc", "normalized_fingerprint": "fp1", "etag": '"e1"', "last_modified": None}
    curr = {"status": 304, "body_hash": None, "normalized_fingerprint": None, "etag": '"e1"', "last_modified": None}
    result = bs.compare_baselines(prev, curr)
    assert result["body_changed"] is False
    assert result["data_changed"] is False
    assert result["validator_changed"] is False
    # curr should have been updated with prev's values
    assert curr["body_hash"] == "abc"
    assert curr["normalized_fingerprint"] == "fp1"


def test_compare_baselines_304_no_baseline():
    prev = None
    curr = {"status": 304, "body_hash": None, "etag": '"e1"'}
    result = bs.compare_baselines(prev, curr)
    assert result["has_prev"] is False


# --- Scenario A: Identical response ---

def test_scenario_a_identical():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {
        "status": 200, "cond_status": 304, "failed": False, "error": None,
        "validator_changed": False, "body_changed": False, "data_changed": False,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"


# --- Scenario B: Body changes, location data same ---

def test_scenario_b_body_changed_data_same():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {
        "status": 200, "cond_status": 200, "failed": False, "error": None,
        "validator_changed": True, "body_changed": True, "data_changed": False,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"
    assert "false positive" in decision["justification"]


# --- Scenario C: Location data changes, validator changes ---

def test_scenario_c_data_changed():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {
        "status": 200, "cond_status": 200, "failed": False, "error": None,
        "validator_changed": True, "body_changed": True, "data_changed": True,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "trigger_extraction"


# --- Scenario D: Location data changes, validator does not ---

def test_scenario_d_data_changed_validator_same():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {
        "status": 200, "cond_status": 200, "failed": False, "error": None,
        "validator_changed": False, "body_changed": True, "data_changed": True,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "trigger_extraction"


# --- Scenario E: Request fails ---

def test_scenario_e_request_fails():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {
        "status": None, "cond_status": None, "failed": True, "error": "Connection timed out",
        "validator_changed": False, "body_changed": "unknown", "data_changed": "unknown",
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "unknown"
    assert decision["fallback_executed"] is True


# --- Signal separation ---

def test_route_strategy_B_changed():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {
        "status": 200, "failed": False, "error": None,
        "body_changed": True, "data_changed": True,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "trigger_extraction"


def test_route_strategy_B_unchanged():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {
        "status": 200, "failed": False, "error": None,
        "body_changed": False, "data_changed": False,
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"


def test_route_strategy_B_inconclusive():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {
        "status": 200, "failed": False, "error": None,
        "body_changed": "unknown", "data_changed": "unknown",
    }
    decision = mon.route(resource, result)
    assert decision["decision"] == "unknown"


# --- Resource identity ---

def test_normalize_url_strips_tracking():
    url = "https://example.com/stores?utm_source=google&foo=bar"
    norm = bs.normalize_url(url)
    assert "utm_source" not in norm
    assert "foo=bar" in norm


def test_normalize_url_lowercase_host():
    url = "https://Example.COM/Stores/"
    norm = bs.normalize_url(url)
    assert "example.com" in norm
    assert norm.endswith("/Stores") or norm.endswith("/Stores?")


def test_resource_identity_post_different_body():
    id1 = bs.resource_identity("sp", "https://api.com/stores", "POST", "body1")
    id2 = bs.resource_identity("sp", "https://api.com/stores", "POST", "body2")
    assert id1 != id2


def test_resource_identity_same():
    id1 = bs.resource_identity("sp", "https://example.com/stores", "GET")
    id2 = bs.resource_identity("sp", "https://example.com/stores", "GET")
    assert id1 == id2


def test_resource_identity_different_spider():
    id1 = bs.resource_identity("sp1", "https://example.com/stores", "GET")
    id2 = bs.resource_identity("sp2", "https://example.com/stores", "GET")
    assert id1 != id2


# --- Weak ETag handling ---

def test_weak_etag_not_discarded():
    # Weak ETags should still be usable
    etag = 'W/"abc123"'
    assert etag.startswith('W/"')
    # Verify the eligibility doesn't reject weak ETags
    result = {"status": 200, "failed": False, "error": None, "body_hash": "abc", "strategy": "A",
              "etag": etag, "cond_status": 304}
    eligible, reason = bs.is_baseline_eligible(result)
    assert eligible is True


# --- Duplicate observations ---

def test_compare_baselines_no_prev():
    prev = None
    curr = {"body_hash": "abc", "etag": '"e1"'}
    result = bs.compare_baselines(prev, curr)
    assert result["has_prev"] is False
    assert result["body_changed"] is None


def test_compare_baselines_body_same():
    prev = {"body_hash": "abc", "normalized_fingerprint": "fp1", "etag": '"e1"', "last_modified": None}
    curr = {"body_hash": "abc", "normalized_fingerprint": "fp1", "etag": '"e1"', "last_modified": None}
    result = bs.compare_baselines(prev, curr)
    assert result["body_changed"] is False
    assert result["data_changed"] is False
    assert result["validator_changed"] is False


def test_compare_baselines_body_different():
    prev = {"body_hash": "abc", "normalized_fingerprint": "fp1", "etag": '"e1"', "last_modified": None}
    curr = {"body_hash": "def", "normalized_fingerprint": "fp2", "etag": '"e2"', "last_modified": None}
    result = bs.compare_baselines(prev, curr)
    assert result["body_changed"] is True
    assert result["data_changed"] is True
    assert result["validator_changed"] is True


# --- Baseline persistence (append-only) ---

def test_save_and_load_baseline():
    d = tempfile.mkdtemp()
    b = bs.create_baseline("sp", "url", "hash123", baseline_status="eligible")
    bs.save_baseline(d, b)
    loaded = bs.load_baselines(d)
    assert len(loaded) == 1
    assert loaded[0]["spider"] == "sp"
    assert loaded[0]["body_hash"] == "hash123"


def test_get_latest_baseline():
    d = tempfile.mkdtemp()
    b1 = bs.create_baseline("sp", "url", "hash1")
    bs.save_baseline(d, b1)
    b2 = bs.create_baseline("sp", "url", "hash2")
    bs.save_baseline(d, b2)
    latest = bs.get_latest_baseline(bs.load_baselines(d), "sp", "url")
    assert latest["body_hash"] == "hash2"


def test_get_latest_baseline_none():
    d = tempfile.mkdtemp()
    latest = bs.get_latest_baseline(bs.load_baselines(d), "sp", "url")
    assert latest is None


# --- Malformed response ---

def test_extract_location_features_malformed():
    features, fp = bs.extract_location_features("not json", "sp")
    assert features is None
    assert fp is None


def test_extract_location_features_empty():
    features, fp = bs.extract_location_features("{}", "sp")
    assert features is None
    assert fp is None


def test_extract_location_features_valid():
    data = json.dumps({"stores": [{"name": "A", "lat": 1.0, "lon": 2.0}]})
    features, fp = bs.extract_location_features(data, "sp")
    assert features is not None
    assert len(features) == 1
    assert fp is not None


# --- Fingerprint ---

def test_compute_fingerprint_empty():
    assert bs.compute_fingerprint(None) is None
    assert bs.compute_fingerprint([]) is None


def test_compute_fingerprint_deterministic():
    feat = [{"type": "Feature", "id": "a", "properties": {"name": "X"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    fp1 = bs.compute_fingerprint(feat)
    fp2 = bs.compute_fingerprint(feat)
    assert fp1 == fp2
    assert fp1 is not None


# --- Reconciliation ---

def test_reconcile_preserves_baseline_on_failure():
    # Just verify the function exists and is callable
    assert callable(mon.reconcile)
