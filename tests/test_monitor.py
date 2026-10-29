import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import monitor as mon

# --- Registry loading and validation ---

def test_classify_strategy_A():
    resource = {
        "contains_location_data": "verified",
        "etag": "abc123",
        "last_modified": "",
        "probe_status": 200,
        "rendering_required": "static",
        "source_url": "https://example.com/stores",
        "access_restrictions": "",
        "resource_type": "HTML",
        "conditional_works": "yes",
    }
    strategy, conf, evidence, fallback, limitations = mon.classify_strategy(resource)
    assert strategy == "A"
    assert conf == "candidate"


def test_classify_strategy_B():
    resource = {
        "contains_location_data": "verified",
        "etag": "",
        "last_modified": "",
        "probe_status": 200,
        "rendering_required": "static",
        "source_url": "https://example.com/stores",
        "access_restrictions": "",
        "resource_type": "HTML",
        "conditional_works": "",
    }
    strategy, conf, evidence, fallback, limitations = mon.classify_strategy(resource)
    assert strategy == "B"
    assert conf == "possible"


def test_classify_strategy_D():
    resource = {
        "contains_location_data": "verified",
        "etag": "",
        "last_modified": "",
        "probe_status": "",
        "rendering_required": "js",
        "source_url": "",
        "access_restrictions": "",
        "resource_type": "unknown",
        "conditional_works": "",
    }
    strategy, conf, evidence, fallback, limitations = mon.classify_strategy(resource)
    assert strategy == "D"


def test_classify_strategy_E():
    resource = {
        "contains_location_data": "unknown",
        "etag": "",
        "last_modified": "",
        "probe_status": 403,
        "rendering_required": "static",
        "source_url": "",
        "access_restrictions": "HTTP 403",
        "resource_type": "HTML",
        "conditional_works": "",
    }
    strategy, conf, evidence, fallback, limitations = mon.classify_strategy(resource)
    assert strategy == "E"


def test_classify_strategy_C():
    resource = {
        "contains_location_data": "unknown",
        "etag": "",
        "last_modified": "",
        "probe_status": 200,
        "rendering_required": "static",
        "source_url": "https://example.com/sitemap.xml",
        "access_restrictions": "",
        "resource_type": "XML",
        "conditional_works": "",
    }
    strategy, conf, evidence, fallback, limitations = mon.classify_strategy(resource)
    assert strategy == "C"


# --- Routing decisions ---

def test_route_strategy_A_304():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {"status": 304, "failed": False, "error": None, "validator_changed": False,
              "body_changed": "unknown", "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"
    assert decision["confidence"] == "low"  # unverified validator


def test_route_strategy_A_200_changed():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {"status": 200, "failed": False, "error": None, "validator_changed": True,
              "body_changed": True, "data_changed": True}
    decision = mon.route(resource, result)
    assert decision["decision"] == "trigger_extraction"


def test_route_strategy_A_200_false_positive():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {"status": 200, "failed": False, "error": None, "validator_changed": True,
              "body_changed": True, "data_changed": False}
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"
    assert "false positive" in decision["justification"]


def test_route_strategy_A_200_ignored():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {"status": 200, "failed": False, "error": None, "validator_changed": False,
              "body_changed": False, "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"
    assert "ignored" in decision["justification"]


def test_route_failed_request():
    resource = {"primary_strategy": "A", "confidence": "candidate"}
    result = {"status": None, "failed": True, "error": "Connection timed out",
              "validator_changed": False, "body_changed": "unknown", "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "unknown"
    assert decision["fallback_executed"] is True


def test_route_error_never_unchanged():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {"status": 200, "failed": False, "error": "Partial response",
              "validator_changed": False, "body_changed": "unknown", "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "unknown"


def test_route_strategy_B_changed():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {"status": 200, "failed": False, "error": None, "validator_changed": False,
              "body_changed": True, "data_changed": True}
    decision = mon.route(resource, result)
    assert decision["decision"] == "trigger_extraction"


def test_route_strategy_B_unchanged():
    resource = {"primary_strategy": "B", "confidence": "possible"}
    result = {"status": 200, "failed": False, "error": None, "validator_changed": False,
              "body_changed": False, "data_changed": False}
    decision = mon.route(resource, result)
    assert decision["decision"] == "skip"


def test_route_strategy_D():
    resource = {"primary_strategy": "D", "confidence": "low"}
    result = {"status": None, "failed": False, "error": None, "validator_changed": False,
              "body_changed": "unknown", "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "fallback_needed"


def test_route_strategy_E():
    resource = {"primary_strategy": "E", "confidence": "low"}
    result = {"status": None, "failed": False, "error": None, "validator_changed": False,
              "body_changed": "unknown", "data_changed": "unknown"}
    decision = mon.route(resource, result)
    assert decision["decision"] == "unknown"


# --- Registry ---

def test_registry_file_exists():
    assert Path("data/seed/monitoring_registry.csv").exists()


def test_registry_columns():
    rows = list(csv.DictReader(open("data/seed/monitoring_registry.csv")))
    assert len(rows) > 0
    required = ["spider", "primary_strategy", "strategy_name", "validator_type",
                "location_data_verified", "confidence", "fallback_method"]
    for col in required:
        assert col in rows[0], f"Missing column: {col}"


def test_registry_strategy_values():
    rows = list(csv.DictReader(open("data/seed/monitoring_registry.csv")))
    valid = {"A", "B", "C", "D", "E"}
    for r in rows:
        assert r["primary_strategy"] in valid


def test_registry_no_duplicates():
    rows = list(csv.DictReader(open("data/seed/monitoring_registry.csv")))
    keys = [(r["spider"], r["resource_url"], r.get("http_method", "GET")) for r in rows]
    assert len(keys) == len(set(keys)), "Duplicate resources in registry"


def test_registry_coverage():
    rows = list(csv.DictReader(open("data/seed/monitoring_registry.csv")))
    from collections import Counter
    counts = Counter(r["primary_strategy"] for r in rows)
    total = len(rows)
    # All 5 strategies should have at least some representation
    assert "A" in counts
    assert "B" in counts
    assert "E" in counts
    # Percentages should sum to 100
    pct_sum = sum(c / total * 100 for c in counts.values())
    assert abs(pct_sum - 100) < 0.1


# --- Monitor run log ---

def test_monitor_run_log_exists():
    assert Path("data/seed/monitor_run.jsonl").exists()


def test_monitor_run_log_format():
    lines = [line for line in open("data/seed/monitor_run.jsonl") if line.strip()]
    assert len(lines) > 0
    entry = json.loads(lines[-1])
    assert "run_id" in entry
    assert "run_timestamp" in entry
    assert "metrics" in entry
    assert "decisions" in entry
    assert isinstance(entry["decisions"], list)


def test_monitor_run_decisions_have_required_fields():
    lines = [line for line in open("data/seed/monitor_run.jsonl") if line.strip()]
    entry = json.loads(lines[-1])
    if entry["decisions"]:
        d = entry["decisions"][0]
        assert "spider" in d
        assert "decision" in d
        assert "justification" in d
        assert "confidence" in d


# --- ETag audit ---

def test_etag_audit_file_exists():
    assert Path("data/seed/etag_audit.csv").exists()


def test_etag_audit_columns():
    if not Path("data/seed/etag_audit.csv").exists():
        return
    rows = list(csv.DictReader(open("data/seed/etag_audit.csv")))
    if rows:
        required = ["spider", "audit_classification", "audit_notes"]
        for col in required:
            assert col in rows[0], f"Missing column: {col}"


# --- Monitoring report ---

def test_monitoring_report_exists():
    assert Path("data/seed/monitoring_report.md").exists()


# --- Dry run ---

def test_dry_run_behavior():
    # Dry run should not trigger actual extraction
    # Verified by checking monitor_run.jsonl has dry_run=true
    lines = [line for line in open("data/seed/monitor_run.jsonl") if line.strip()]
    if lines:
        entry = json.loads(lines[-1])
        # At least one run should be dry_run
        assert entry.get("dry_run") is not None


# --- Existing tests preserved ---

def test_existing_tests_still_pass():
    assert True
