import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_seed as cs
import signal_compare as sc
import synthetic_fixtures as sf


# --- Scenario 1: Store added ---
def test_scenario_store_added():
    prev, curr, expected = sf.scenario_store_added()
    assert len(prev) == 1
    assert len(curr) == 2
    assert expected == "added"
    # Match by ID
    prev_ids = {f["id"] for f in prev}
    curr_ids = {f["id"] for f in curr}
    added = curr_ids - prev_ids
    assert len(added) == 1
    assert "b" in added


# --- Scenario 2: Store removed ---
def test_scenario_store_removed():
    prev, curr, expected = sf.scenario_store_removed()
    assert len(prev) == 2
    assert len(curr) == 1
    assert expected == "removed"
    prev_ids = {f["id"] for f in prev}
    curr_ids = {f["id"] for f in curr}
    removed = prev_ids - curr_ids
    assert len(removed) == 1
    assert "b" in removed


# --- Scenario 3: Address changed ---
def test_scenario_address_changed():
    prev, curr, expected = sf.scenario_address_changed()
    assert expected == "modified_address"
    prev_feat = prev[0]
    curr_feat = curr[0]
    assert prev_feat["id"] == curr_feat["id"]
    p_props = prev_feat["properties"]
    c_props = curr_feat["properties"]
    assert p_props["addr:street_address"] != c_props["addr:street_address"]
    assert p_props["addr:street_address"] == "1 Main St"
    assert c_props["addr:street_address"] == "99 New Ave"


# --- Scenario 4: Coordinate changed ---
def test_scenario_coord_changed():
    prev, curr, expected = sf.scenario_coord_changed()
    assert expected == "modified_coordinates"
    prev_coords = prev[0]["geometry"]["coordinates"]
    curr_coords = curr[0]["geometry"]["coordinates"]
    assert prev_coords != curr_coords
    assert prev_coords == [1.0, 2.0]
    assert curr_coords == [1.5, 2.5]


# --- Scenario 5: Irrelevant banner change (location data same) ---
def test_scenario_irrelevant_banner():
    prev, curr, expected = sf.scenario_irrelevant_banner_change()
    assert expected == "no_change"
    assert prev == curr  # identical location data


# --- Scenario 6: ETag changed, no location change ---
def test_scenario_etag_changed_no_data():
    prev, curr, expected = sf.scenario_etag_changed_no_data_change()
    assert expected == "no_change"
    assert prev == curr


# --- Scenario 7: Location changed, validator unchanged ---
def test_scenario_data_changed_validator_same():
    prev, curr, expected = sf.scenario_data_changed_validator_same()
    assert expected == "modified"
    # Data changed but IDs same
    assert prev[0]["id"] == curr[0]["id"]
    assert prev[0]["properties"]["addr:street_address"] != curr[0]["properties"]["addr:street_address"]


# --- Scenario 8: Weak ETag ---
def test_scenario_weak_etag():
    etag, strength = sf.scenario_weak_etag()
    assert etag.startswith('W/"')
    assert strength == "weak"
    # Weak ETag should still be usable (truthy)
    assert bool(etag) is True


# --- Scenario 9: Server ignores conditional headers ---
def test_scenario_server_ignores_condition():
    body1, body2 = sf.scenario_server_ignores_condition()
    assert body1 == body2  # identical body
    # But server returns 200 instead of 304


# --- Scenario 10: Failed request ---
def test_scenario_failed_request():
    status, error = sf.scenario_failed_request()
    assert status is None
    assert error is not None
    # Failed request should never be "unchanged"


# --- Scenario 11: Inconclusive observation ---
def test_scenario_inconclusive():
    statuses = sf.scenario_inconclusive()
    assert 304 in statuses
    assert 200 in statuses
    # Mixed results → inconclusive


# --- Scenario 12: Duplicate/unstable IDs ---
def test_scenario_unstable_ids():
    prev, curr, expected = sf.scenario_unstable_ids()
    assert expected == "renamed"
    assert prev[0]["id"] != curr[0]["id"]
    # But name and address are the same
    assert prev[0]["properties"]["name"] == curr[0]["properties"]["name"]
    assert prev[0]["properties"]["addr:street_address"] == curr[0]["properties"]["addr:street_address"]


# --- Scenario 13: Malformed GeoJSON ---
def test_scenario_malformed_geojson():
    prev, curr = sf.scenario_malformed_geojson()
    # Validate that normalize_feature handles missing properties
    norm, _ = cs.normalize_feature(
        {"type": "Feature", "properties": None, "geometry": None},
        "sp", "r", "ts", "f"
    )
    assert norm["name"] is None
    assert norm["lat"] is None


# --- Signal comparison classification tests ---
def test_classify_pair_correct_negative():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 304, "etag": '"abc"', "last_modified": None,
            "response_body_hash": None, "error": None, "normalized_fingerprint": "fp1"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "correct_negative"
    assert result["validator_changed"] is False


def test_classify_pair_correct_positive():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 200, "etag": '"def"', "last_modified": None,
            "response_body_hash": "hash2", "error": None, "normalized_fingerprint": "fp2"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "correct_positive"
    assert result["validator_changed"] is True


def test_classify_pair_false_positive():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 200, "etag": '"def"', "last_modified": None,
            "response_body_hash": "hash2", "error": None, "normalized_fingerprint": "fp1"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "false_positive"


def test_classify_pair_potential_missed_change():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 304, "etag": '"abc"', "last_modified": None,
            "response_body_hash": None, "error": None, "normalized_fingerprint": "fp2"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "potential_missed_change"


def test_classify_pair_request_failed():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": None, "etag": None, "last_modified": None,
            "response_body_hash": None, "error": "Connection timed out", "normalized_fingerprint": None}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "request_failed"


def test_classify_pair_validator_unavailable():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": None, "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 200, "etag": None, "last_modified": None,
            "response_body_hash": "hash2", "error": None, "normalized_fingerprint": "fp2"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "validator_unavailable"


def test_classify_pair_weak_etag():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": 'W/"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 304, "etag": 'W/"abc"', "last_modified": None,
            "response_body_hash": None, "error": None, "normalized_fingerprint": "fp1"}
    result = sc.classify_pair(prev, curr)
    assert result["classification"] == "correct_negative"
    assert "[weak ETag]" in result["notes"]


def test_classify_pair_server_ignores_condition():
    prev = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-01T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    curr = {"spider": "s", "resource_url": "u", "timestamp": "2026-01-02T00:00:00Z",
            "status_code": 200, "etag": '"abc"', "last_modified": None,
            "response_body_hash": "hash1", "error": None, "normalized_fingerprint": "fp1"}
    result = sc.classify_pair(prev, curr)
    # Validator unchanged, server returned 200 with identical body → ignored condition
    assert result["classification"] == "ignored_condition"
    assert result["validator_changed"] is False


def test_signal_compare_group_observations():
    obs = [
        {"spider": "s1", "resource_url": "u1", "timestamp": "2026-01-01T00:00:00Z", "status_code": 200},
        {"spider": "s1", "resource_url": "u1", "timestamp": "2026-01-02T00:00:00Z", "status_code": 304},
        {"spider": "s2", "resource_url": "u2", "timestamp": "2026-01-01T00:00:00Z", "status_code": 200},
    ]
    groups = sc.group_observations(obs)
    assert len(groups) == 2
    assert len(groups[("s1", "u1")]) == 2
    assert len(groups[("s2", "u2")]) == 1


def test_live_observe_probe_resource_structure():
    import live_observe as lo
    # Test that probe_resource returns the right structure (will make network call)
    resource = {
        "spider": "test",
        "original_url": "https://httpbin.org/status/200",
        "http_method": "GET",
    }
    obs = lo.probe_resource(resource, timeout=10)
    assert "observation_id" in obs
    assert "timestamp" in obs
    assert "status_code" in obs
    assert "response_body_hash" in obs
    assert "suitable_for_comparison" in obs
    assert "bytes_transferred" in obs
    assert "elapsed_ms" in obs


def test_synthetic_fixtures_generate():
    import tempfile, os
    d = tempfile.mkdtemp()
    sf.generate_all(d)
    assert os.path.exists(os.path.join(d, "store_added_prev.geojson"))
    assert os.path.exists(os.path.join(d, "store_added_curr.geojson"))
    assert os.path.exists(os.path.join(d, "store_added_expected.txt"))
    assert os.path.exists(os.path.join(d, "malformed_prev.geojson"))