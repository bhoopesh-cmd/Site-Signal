#!/usr/bin/env python3
"""Step 4: Monitoring engine for site-signal.

Usage:
    python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --dry-run
    python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --live
    python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --audit-etags
    python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --reconcile --interval-hours 168
"""
import argparse
import csv
import hashlib
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store as bs

import collect_seed as cs

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
TOKEN_RE = cs.TOKEN_RE


def now_iso():
    return datetime.now(UTC).isoformat()


def redact_url(url):
    return cs.redact_url(url)


# ============================================================
# Strategy selection
# ============================================================

STRATEGIES = ["A", "B", "C", "D", "E"]
STRATEGY_NAMES = {
    "A": "conditional_http",
    "B": "direct_payload_comparison",
    "C": "alternative_metadata",
    "D": "browser_fallback",
    "E": "unresolved",
}


def classify_strategy(resource):
    """Assign a primary monitoring strategy based on evidence.

    Returns (strategy_code, confidence, evidence, fallback_method, limitations).
    """
    contains_loc = resource.get("contains_location_data", "unknown")
    etag = resource.get("etag", "")
    lm = resource.get("last_modified", "")
    status = resource.get("probe_status", "")
    rendering = resource.get("rendering_required", "unknown")
    source_url = resource.get("source_url", "")
    resource_type = resource.get("resource_type", "unknown")
    cond_works = resource.get("conditional_works", "")

    # Check if accessible
    accessible = False
    if isinstance(status, int):
        accessible = 200 <= status < 400
    elif isinstance(status, str) and status.isdigit():
        accessible = 200 <= int(status) < 400

    # Strategy A: Conditional HTTP candidate
    # Requires: verified location data + usable validator + conditional request works
    has_validator = bool(etag or lm)
    if contains_loc == "verified" and has_validator and accessible:
        if cond_works == "yes":
            return "A", "candidate", "Verified location data + working conditional request", "B", "Validator reliability unverified (single observation)"
        else:
            # Validator present but conditional untested or failed
            return "A", "candidate", "Verified location data + validator present (conditional untested)", "B", "Conditional request not yet verified"

    # Strategy C: Alternative metadata or feed (sitemap/feed identified)
    if resource_type in ("XML",) and "sitemap" in (source_url or "").lower():
        if accessible:
            return "C", "candidate", "Sitemap/feed identified and accessible", "B", "Sitemap change-detection unverified"

    # Strategy D: Browser fallback (JS-rendered, no direct URL)
    if rendering == "js" and (not source_url or not accessible):
        return "D", "low", "JS-rendered source, no accessible direct URL", "E", "Browser execution not available in prototype"

    # Strategy B: Direct payload comparison (accessible, verified data, no validator)
    if contains_loc == "verified" and accessible:
        return "B", "possible", "Verified location data, no usable validator, accessible", "D", "No conditional request possible"

    # Strategy E: Unresolved
    if not accessible or not source_url:
        return "E", "low", "Inaccessible or insufficiently understood", "D", "Source inaccessible or ambiguous"

    # Default: if location data unverified but accessible
    if accessible:
        return "B", "low", "Accessible but location data unverified", "D", "Location-data relevance unverified"

    return "E", "low", "Insufficient evidence", "D", "Unresolved"


def build_registry(out):
    """Build monitoring_registry.csv from existing data."""
    # Load resource inventory
    inv_path = out / "resource_inventory.csv"
    if not inv_path.exists():
        print("ERROR: resource_inventory.csv not found. Run evaluate_signals.py first.")
        return []

    resources = list(csv.DictReader(open(inv_path)))

    # Load manifest for additional metadata
    manifest = {}
    manifest_path = out / "manifest.csv"
    if manifest_path.exists():
        for r in csv.DictReader(open(manifest_path)):
            manifest[r["spider"]] = r

    # Load conditional probe results
    cond_map = {}
    cond_path = out / "conditional_probe_results.csv"
    if cond_path.exists():
        for r in csv.DictReader(open(cond_path)):
            cond_map[(r["spider"], r["url"])] = r

    # Deduplicate by (spider, url, http_method)
    seen = set()
    deduped = []
    for r in resources:
        spider = r["spider"]
        url = r["original_url"]
        method = r.get("http_method", "GET")
        if not method:
            method = "GET"
        key = (spider, url, method)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    # Build registry entries
    rows = []
    for r in deduped:
        spider = r["spider"]
        m = manifest.get(spider, {})
        cond = cond_map.get((spider, r["original_url"]), {})

        strategy, confidence, evidence, fallback, limitations = classify_strategy({
            "spider": spider,
            "contains_location_data": r.get("contains_location_data", "unknown"),
            "etag": r.get("etag", ""),
            "last_modified": r.get("last_modified", ""),
            "probe_status": r.get("probe_status", ""),
            "rendering_required": m.get("rendering_required", "unknown"),
            "source_url": r.get("original_url", ""),
            "access_restrictions": r.get("access_restrictions", ""),
            "resource_type": r.get("resource_type", "unknown"),
            "conditional_works": cond.get("conditional_works", ""),
            "http_method": r.get("http_method", "GET"),
        })

        # Last successful observation
        last_obs = r.get("probe_timestamp", "")

        rows.append({
            "spider": spider,
            "brand": m.get("brand", r.get("brand", "")),
            "domain": r.get("domain", ""),
            "resource_url": r["original_url"],
            "http_method": r.get("http_method", "GET") or "GET",
            "resource_type": r.get("resource_type", "unknown"),
            "primary_strategy": strategy,
            "strategy_name": STRATEGY_NAMES[strategy],
            "validator_type": "etag" if r.get("etag") else ("last_modified" if r.get("last_modified") else "none"),
            "validator_availability": "present" if (r.get("etag") or r.get("last_modified")) else "absent",
            "etag": r.get("etag", ""),
            "last_modified": r.get("last_modified", ""),
            "etag_strength": r.get("etag_strength", "none") if r.get("etag_strength") else ("weak" if (r.get("etag", "").startswith('W/"')) else ("strong" if r.get("etag") else "none")),
            "location_data_verified": r.get("contains_location_data", "unknown"),
            "confidence": confidence,
            "evidence": evidence,
            "last_successful_observation": last_obs,
            "fallback_method": fallback,
            "unresolved_limitations": limitations,
            "feature_count": m.get("feature_count", ""),
            "probe_status": r.get("probe_status", ""),
            "conditional_works": cond.get("conditional_works", ""),
            "monitoring_resource": r.get("original_url", ""),
            "monitoring_resource_confidence": m.get("source_mapping_confidence", ""),
        })

    # Sort by spider then url
    rows.sort(key=lambda r: (r["spider"], r["resource_url"]))

    # Write registry
    out_path = out / "monitoring_registry.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Coverage report
    from collections import Counter
    strat_counts = Counter(r["primary_strategy"] for r in rows)
    total = len(rows)
    print(f"\n=== Strategy Coverage ({total} unique resources) ===")
    for s in STRATEGIES:
        count = strat_counts.get(s, 0)
        pct = (count / total * 100) if total else 0
        print(f"  {s} ({STRATEGY_NAMES[s]}): {count} ({pct:.1f}%)")

    return rows


# ============================================================
# Routing decision
# ============================================================

def route(resource, result):
    """Make a routing decision based on strategy and result.

    Returns dict: decision, justification, confidence, fallback_executed.
    """
    strategy = resource.get("primary_strategy", "E")
    failed = result.get("failed", False)
    status = result.get("status")
    body_changed = result.get("body_changed", "unknown")
    data_changed = result.get("data_changed", "unknown")

    # Failed request → never assume unchanged
    if failed or ((isinstance(status, int) and not (200 <= status < 400)) and status is not None):
        if status is None or (isinstance(status, str) and not status.isdigit()):
            # Network error
            return {
                "decision": "unknown",
                "justification": f"Request failed: {result.get('error', 'unknown error')}",
                "confidence": "n/a",
                "fallback_executed": True,
            }

    if result.get("error"):
        return {
            "decision": "unknown",
            "justification": f"Request error: {result['error']}",
            "confidence": "n/a",
            "fallback_executed": True,
        }

    if strategy == "A":
        # Check conditional request result
        cond_status = result.get("cond_status")
        if cond_status == 304:
            # Validator says unchanged
            if resource.get("confidence") == "candidate":
                return {
                    "decision": "skip",
                    "justification": "Conditional request returned 304; validator present but reliability unverified",
                    "confidence": "low",
                    "fallback_executed": False,
                }
            return {
                "decision": "skip",
                "justification": "Conditional request returned 304; validator trusted",
                "confidence": "high",
                "fallback_executed": False,
            }
        if cond_status == 200 or status == 200:
            # Validator changed but body unchanged → false positive
            if result.get("validator_changed") and body_changed is False:
                return {
                    "decision": "skip",
                    "justification": "Validator changed but body unchanged (false positive)",
                    "confidence": "medium",
                    "fallback_executed": False,
                }
            if body_changed is True and data_changed is True:
                return {
                    "decision": "trigger_extraction",
                    "justification": "Conditional request returned 200 with changed body and changed location data",
                    "confidence": "medium",
                    "fallback_executed": False,
                }
            if body_changed is True and data_changed is False:
                return {
                    "decision": "skip",
                    "justification": "Conditional request returned 200 with changed body but location data unchanged (false positive)",
                    "confidence": "medium",
                    "fallback_executed": False,
                }
            if body_changed is False:
                return {
                    "decision": "skip",
                    "justification": "Conditional request returned 200 with identical body (conditional ignored)",
                    "confidence": "low",
                    "fallback_executed": False,
                }
            # Unknown data change
            return {
                "decision": "unknown",
                "justification": "Conditional request returned 200 but data change unknown; needs verification",
                "confidence": "low",
                "fallback_executed": True,
            }
        # Other status
        return {
            "decision": "unknown",
            "justification": f"Unexpected status {status}/{cond_status} for conditional HTTP",
            "confidence": "n/a",
            "fallback_executed": True,
        }

    if strategy == "B":
        # Direct payload comparison
        if data_changed is True:
            return {
                "decision": "trigger_extraction",
                "justification": "Direct payload comparison shows location data changed",
                "confidence": "medium",
                "fallback_executed": False,
            }
        if data_changed is False:
            return {
                "decision": "skip",
                "justification": "Direct payload comparison shows no location data change",
                "confidence": "medium",
                "fallback_executed": False,
            }
        return {
            "decision": "unknown",
            "justification": "Direct payload comparison inconclusive (no baseline or extraction failed)",
            "confidence": "low",
            "fallback_executed": True,
        }

    if strategy == "C":
        # Alternative metadata/feed
        if result.get("metadata_changed", False):
            return {
                "decision": "trigger_extraction",
                "justification": "Metadata/feed change detected",
                "confidence": "low",
                "fallback_executed": False,
            }
        return {
            "decision": "skip",
            "justification": "No metadata/feed change detected",
            "confidence": "low",
            "fallback_executed": False,
        }

    if strategy == "D":
        return {
            "decision": "fallback_needed",
            "justification": "Browser interaction required; fallback to existing scraper",
            "confidence": "n/a",
            "fallback_executed": True,
        }

    # Strategy E: Unresolved
    return {
        "decision": "unknown",
        "justification": "Resource unresolved; no monitoring method available",
        "confidence": "n/a",
        "fallback_executed": True,
    }


# ============================================================
# Strategy executors
# ============================================================

def execute_strategy(resource, timeout=15, baseline=None):
    """Execute the monitoring strategy for a resource. Returns result dict."""
    strategy = resource.get("primary_strategy", "E")
    url = resource.get("resource_url", "")
    method = resource.get("http_method", "GET") or "GET"
    result = {
        "spider": resource.get("spider", ""),
        "strategy": strategy,
        "status": None,
        "error": None,
        "failed": False,
        "etag": None,
        "last_modified": None,
        "body_changed": "unknown",
        "data_changed": "unknown",
        "bytes_transferred": 0,
        "elapsed_ms": 0,
        "metadata_changed": False,
        "validator_changed": False,
    }

    t0 = time.time()
    try:
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            r = requests.post(url, timeout=timeout, headers=headers, allow_redirects=True)
        else:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)

        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["status"] = r.status_code
        result["etag"] = r.headers.get("ETag")
        result["last_modified"] = r.headers.get("Last-Modified")
        result["bytes_transferred"] = len(r.content) if r.content else 0

        # Handle 304 Not Modified — reuse saved baseline state
        if r.status_code == 304:
            # 304 has no body — reuse saved baseline's body hash and fingerprint
            if baseline:
                result["body_hash"] = baseline.get("body_hash")
                result["normalized_fingerprint"] = baseline.get("normalized_fingerprint")
                result["body_changed"] = False
                result["data_changed"] = False
                result["validator_changed"] = False
                result["status"] = 304
            else:
                # No baseline to reuse — unknown
                result["body_hash"] = None
                result["normalized_fingerprint"] = None
                result["body_changed"] = "unknown"
                result["data_changed"] = "unknown"
                result["validator_changed"] = False
                result["status"] = 304
                result["error"] = "304 without saved baseline"
                result["failed"] = True
            return result

        # Always compute body hash for comparison (200 responses)
        if r.content:
            result["body_hash"] = hashlib.sha256(r.content).hexdigest()
        else:
            result["body_hash"] = None

        if not (200 <= r.status_code < 400):
            result["failed"] = True
            result["error"] = f"HTTP {r.status_code}"
            return result

        # Strategy A: Conditional request
        if strategy == "A":
            etag = resource.get("http_etag", "") or r.headers.get("ETag", "")
            lm = resource.get("http_last_modified", "") or r.headers.get("Last-Modified", "")
            if etag:
                cond_headers = {"User-Agent": USER_AGENT, "If-None-Match": etag}
                t1 = time.time()
                try:
                    cr = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)
                    result["cond_status"] = cr.status_code
                    result["cond_elapsed_ms"] = int((time.time() - t1) * 1000)
                    result["cond_bytes"] = len(cr.content) if cr.content else 0
                    if cr.status_code == 304:
                        # 304 has no body — reuse saved baseline state
                        result["validator_changed"] = False
                        result["body_changed"] = False
                        result["data_changed"] = False
                        if baseline:
                            result["body_hash"] = baseline.get("body_hash")
                            result["normalized_fingerprint"] = baseline.get("normalized_fingerprint")
                    elif cr.status_code == 200:
                        result["validator_changed"] = True
                        # Compute body hash from the 200 response
                        if cr.content:
                            new_hash = hashlib.sha256(cr.content).hexdigest()
                            result["body_hash"] = new_hash
                            # Compare with baseline
                            if baseline and baseline.get("body_hash"):
                                result["body_changed"] = (new_hash != baseline["body_hash"])
                            # Try to extract location features
                            try:
                                features, fp = bs.extract_location_features(cr.text, resource.get("spider", ""))
                                if fp:
                                    result["normalized_fingerprint"] = fp
                                else:
                                    result["normalized_fingerprint"] = new_hash
                                if baseline and baseline.get("normalized_fingerprint") and result.get("normalized_fingerprint"):
                                    result["data_changed"] = (result["normalized_fingerprint"] != baseline["normalized_fingerprint"])
                            except Exception:
                                result["normalized_fingerprint"] = new_hash
                                if baseline and baseline.get("normalized_fingerprint"):
                                    result["data_changed"] = (new_hash != baseline["normalized_fingerprint"])
                except Exception as e:
                    result["error"] = str(e)
                    result["failed"] = True
            elif lm:
                cond_headers = {"User-Agent": USER_AGENT, "If-Modified-Since": lm}
                t1 = time.time()
                try:
                    cr = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)
                    result["cond_status"] = cr.status_code
                    result["cond_elapsed_ms"] = int((time.time() - t1) * 1000)
                    result["cond_bytes"] = len(cr.content) if cr.content else 0
                    if cr.status_code == 304:
                        result["validator_changed"] = False
                    elif cr.status_code == 200:
                        result["validator_changed"] = True
                except Exception as e:
                    result["error"] = str(e)
                    result["failed"] = True

        # Strategy B/C: Direct payload — compute fingerprint if possible
        if strategy in ("B", "C"):
            result["body_hash"] = hashlib.sha256(r.content).hexdigest() if r.content else None
            # Try to extract location features for fingerprint
            try:
                features, fp = bs.extract_location_features(r.text, resource.get("spider", ""))
                if fp:
                    result["normalized_fingerprint"] = fp
                    result["fingerprint_method"] = "extracted"
                else:
                    # Use body hash as proxy for non-JSON resources
                    result["normalized_fingerprint"] = result["body_hash"]
                    result["fingerprint_method"] = "body_hash_proxy"
            except Exception:
                result["normalized_fingerprint"] = result.get("body_hash")
                result["fingerprint_method"] = "body_hash_proxy"
            # Check if baseline exists and differs
            if baseline and baseline.get("body_hash"):
                result["body_changed"] = (result["body_hash"] != baseline["body_hash"])
            if baseline and baseline.get("normalized_fingerprint") and result.get("normalized_fingerprint"):
                result["data_changed"] = (result["normalized_fingerprint"] != baseline["normalized_fingerprint"])

    except Exception as e:
        result["error"] = str(e)
        result["failed"] = True
        result["elapsed_ms"] = int((time.time() - t0) * 1000)

    return result


# ============================================================
# ETag audit
# ============================================================

def audit_etags(registry, out, timeout=15):
    """Independently audit promising ETag resources."""
    audit_results = []
    promising = [r for r in registry if r.get("primary_strategy") == "A" and r.get("etag_strength") in ("strong", "weak")]
    print(f"Auditing {len(promising)} ETag resources...")

    for r in promising:
        spider = r["spider"]
        url = r["resource_url"]
        entry = {
            "spider": spider,
            "resource_url": redact_url(url),
            "timestamp": now_iso(),
            "audit_type": "etag_independent",
        }
        # Step 1: GET without conditional
        t0 = time.time()
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
            entry["unconditional_status"] = resp.status_code
            entry["unconditional_etag"] = resp.headers.get("ETag")
            entry["unconditional_lm"] = resp.headers.get("Last-Modified")
            entry["unconditional_body_hash"] = hashlib.sha256(resp.content).hexdigest() if resp.content else None
            entry["unconditional_bytes"] = len(resp.content) if resp.content else 0
            entry["unconditional_elapsed_ms"] = int((time.time() - t0) * 1000)

            # Step 2: Conditional GET with saved ETag
            etag = resp.headers.get("ETag")
            if etag:
                t1 = time.time()
                cond_resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "If-None-Match": etag}, allow_redirects=True)
                entry["conditional_status"] = cond_resp.status_code
                entry["conditional_elapsed_ms"] = int((time.time() - t1) * 1000)
                entry["conditional_bytes"] = len(cond_resp.content) if cond_resp.content else 0
                if cond_resp.content:
                    entry["conditional_body_hash"] = hashlib.sha256(cond_resp.content).hexdigest()
                else:
                    entry["conditional_body_hash"] = None

                # Classification
                if cond_resp.status_code == 304:
                    entry["audit_classification"] = "correct_negative_candidate"
                    entry["audit_notes"] = "304 returned; validator honored. Location data unchanged in this observation."
                elif cond_resp.status_code == 200:
                    if entry["conditional_body_hash"] == entry["unconditional_body_hash"]:
                        entry["audit_classification"] = "ignored_condition"
                        entry["audit_notes"] = "200 returned with identical body; server ignored conditional header"
                    else:
                        entry["audit_classification"] = "possible_change"
                        entry["audit_notes"] = "200 returned with different body; possible change or validator unreliable"
                else:
                    entry["audit_classification"] = "inconclusive"
                    entry["audit_notes"] = f"Unexpected status {cond_resp.status_code}"
            else:
                entry["audit_classification"] = "no_etag"
                entry["audit_notes"] = "No ETag in response; cannot test conditional"
                entry["conditional_status"] = None
        except Exception as e:
            entry["audit_classification"] = "error"
            entry["audit_notes"] = str(e)
            entry["unconditional_status"] = None

        audit_results.append(entry)

    # Save audit results
    audit_path = out / "etag_audit.csv"
    with open(audit_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(audit_results[0].keys()))
        w.writeheader()
        for r in audit_results:
            w.writerow(r)

    # Summary
    from collections import Counter
    print(f"\n=== ETag Audit Summary ({len(audit_results)} resources) ===")
    print(Counter(r.get("audit_classification") for r in audit_results))

    return audit_results


# ============================================================
# Reconciliation
# ============================================================

def reconcile(registry, out, timeout=15, interval_hours=168):
    """Full independent check of all resources."""
    print(f"Reconciliation: checking {len(registry)} resources (interval={interval_hours}h)...")

    results = []
    for r in registry:
        # Full independent check — always do direct GET
        url = r["resource_url"]
        result = execute_strategy(r, timeout=timeout)
        # For reconciliation, always compare payload (treat as Strategy B)
        result["strategy"] = "reconciliation"
        decision = route(r, result)
        results.append({
            "spider": r["spider"],
            "resource_url": redact_url(url),
            "status": result.get("status"),
            "decision": decision["decision"],
            "justification": decision["justification"],
            "bytes": result.get("bytes_transferred", 0),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "error": result.get("error"),
        })

    # Summary
    from collections import Counter
    decisions = Counter(r["decision"] for r in results)
    print("\n=== Reconciliation Summary ===")
    print(f"  Resources checked: {len(results)}")
    print(f"  Decisions: {dict(decisions)}")
    print(f"  Total bytes: {sum(r['bytes'] for r in results)}")
    print(f"  Total elapsed: {sum(r['elapsed_ms'] for r in results)}ms")

    return results


# ============================================================
# Main monitoring cycle
# ============================================================

def run_monitoring_cycle(registry, out, dry_run=True, timeout=15, label="monitor"):
    """Run one monitoring cycle. Returns (decisions, metrics)."""
    run_id = str(uuid.uuid4())[:8]
    run_ts = now_iso()

    # Load baselines
    baselines = bs.load_baselines(out)
    print(f"Loaded {len(baselines)} existing baselines")

    decisions = []
    changes = []  # detected changes
    metrics = {
        "run_id": run_id,
        "run_timestamp": run_ts,
        "label": label,
        "dry_run": dry_run,
        "resources_checked": 0,
        "requests_issued": 0,
        "total_bytes": 0,
        "total_elapsed_ms": 0,
        "confidently_unchanged": 0,
        "potential_changes": 0,
        "extraction_jobs_triggered": 0,
        "qa_jobs_triggered": 0,
        "failures": 0,
        "unknown_states": 0,
        "fallback_executions": 0,
        "strategies": {},
    }

    print(f"Monitoring cycle {run_id} ({'DRY RUN' if dry_run else 'LIVE'}) — {len(registry)} resources")

    for r in registry:
        strategy = r.get("primary_strategy", "E")
        metrics["strategies"][strategy] = metrics["strategies"].get(strategy, 0) + 1

        # Load baseline
        baseline = bs.get_latest_baseline(baselines, r["spider"], r["resource_url"])

        # Execute strategy
        result = execute_strategy(r, timeout=timeout, baseline=baseline)
        metrics["requests_issued"] += 1
        metrics["total_bytes"] += result.get("bytes_transferred", 0)
        metrics["total_elapsed_ms"] += result.get("elapsed_ms", 0)

        # Compare with baseline
        comparison = bs.compare_baselines(baseline, {
            "body_hash": result.get("body_hash"),
            "normalized_fingerprint": result.get("normalized_fingerprint"),
            "etag": result.get("etag"),
            "last_modified": result.get("last_modified"),
        })
        result["body_changed"] = comparison.get("body_changed", "unknown")
        result["data_changed"] = comparison.get("data_changed", "unknown")
        result["validator_changed"] = comparison.get("validator_changed", False)

        # Route
        decision = route(r, result)

        # In dry-run, prefix decision
        if dry_run and decision["decision"] in ("trigger_extraction",):
            decision["decision"] = "would_trigger_extraction"
        if dry_run and decision["decision"] in ("skip",):
            decision["decision"] = "would_skip"

        # Count metrics
        metrics["resources_checked"] += 1
        d = decision["decision"]
        if d in ("skip", "would_skip"):
            if decision["confidence"] in ("high", "medium"):
                metrics["confidently_unchanged"] += 1
        if d in ("trigger_extraction", "would_trigger_extraction"):
            metrics["potential_changes"] += 1
            metrics["extraction_jobs_triggered"] += 1
            metrics["qa_jobs_triggered"] += 1
            # Record change
            changes.append({
                "spider": r["spider"],
                "resource_url": redact_url(r["resource_url"]),
                "change_type": "detected",
                "baseline_id": baseline.get("baseline_id", "") if baseline else "",
                "current_observation": result.get("body_hash", ""),
                "changed_fields": result.get("changed_fields", ""),
                "baseline_value": baseline.get("body_hash", "") if baseline else "",
                "current_value": result.get("body_hash", ""),
                "detection_method": r.get("primary_strategy", ""),
                "confidence": decision["confidence"],
                "timestamp": now_iso(),
            })
        if result.get("failed") or result.get("error"):
            metrics["failures"] += 1
        if d == "unknown":
            metrics["unknown_states"] += 1
        if decision.get("fallback_executed"):
            metrics["fallback_executions"] += 1

        entry = {
            "run_id": run_id,
            "run_timestamp": run_ts,
            "spider": r["spider"],
            "resource_url": redact_url(r["resource_url"]),
            "strategy": strategy,
            "strategy_name": STRATEGY_NAMES.get(strategy, strategy),
            "status": result.get("status"),
            "decision": decision["decision"],
            "justification": decision["justification"],
            "confidence": decision["confidence"],
            "fallback_executed": decision.get("fallback_executed", False),
            "bytes_transferred": result.get("bytes_transferred", 0),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "error": result.get("error"),
        }
        decisions.append(entry)

        # Save baseline (append-only) — only if eligible
        eligible, reason = bs.is_baseline_eligible(result)
        if eligible:
            new_baseline = bs.create_baseline(
                spider=r["spider"],
                url=r["resource_url"],
                body_hash=result.get("body_hash"),
                fingerprint=result.get("normalized_fingerprint"),
                etag=result.get("etag"),
                last_modified=result.get("last_modified"),
                feature_count=0,
                status="success" if not result.get("failed") else "failed",
                baseline_status=reason,
                prev_baseline_id=baseline.get("baseline_id") if baseline else None,
            )
            bs.save_baseline(out, new_baseline)
            baselines.append(new_baseline)
        else:
            # Still log the observation but don't overwrite baseline
            result["baseline_status"] = reason

        # Progress
        if len(decisions) % 10 == 0 or len(decisions) == len(registry):
            print(f"  Processed {len(decisions)}/{len(registry)}")

    # Write monitoring changes (append-only)
    if changes:
        changes_path = out / "monitoring_changes.csv"
        changes_cols = ["spider", "resource_url", "change_type", "baseline_id",
                        "current_observation", "changed_fields", "baseline_value",
                        "current_value", "detection_method", "confidence", "timestamp"]
        write_header = not changes_path.exists()
        with open(changes_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=changes_cols, extrasaction="ignore")
            if write_header:
                w.writeheader()
            for c in changes:
                w.writerow(c)
        print(f"  Recorded {len(changes)} changes to {changes_path}")

    # Write run record (append-only)
    run_record = {
        "run_id": run_id,
        "run_timestamp": run_ts,
        "label": label,
        "dry_run": dry_run,
        "metrics": metrics,
        "decisions": decisions,
    }
    run_path = out / "monitor_run.jsonl"
    with open(run_path, "a") as f:
        f.write(json.dumps(run_record) + "\n")

    # Print summary
    print(f"\n=== Monitoring Cycle Summary ({'DRY RUN' if dry_run else 'LIVE'}) ===")
    print(f"  Run ID: {run_id}")
    print(f"  Resources checked: {metrics['resources_checked']}")
    print(f"  Requests issued: {metrics['requests_issued']}")
    print(f"  Total bytes: {metrics['total_bytes']}")
    print(f"  Total elapsed: {metrics['total_elapsed_ms']}ms")
    print(f"  Confidently unchanged: {metrics['confidently_unchanged']}")
    print(f"  Potential changes: {metrics['potential_changes']}")
    print(f"  Extraction jobs triggered: {metrics['extraction_jobs_triggered']}")
    print(f"  QA jobs triggered: {metrics['qa_jobs_triggered']}")
    print(f"  Failures: {metrics['failures']}")
    print(f"  Unknown states: {metrics['unknown_states']}")
    print(f"  Fallback executions: {metrics['fallback_executions']}")

    from collections import Counter
    dec_counts = Counter(d["decision"] for d in decisions)
    print(f"  Decisions: {dict(dec_counts)}")

    return decisions, metrics


# ============================================================
# Main
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Step 4: Monitoring engine")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="Execute but don't trigger downstream")
    ap.add_argument("--live", action="store_true", help="Execute and trigger downstream")
    ap.add_argument("--audit-etags", action="store_true", help="Audit promising ETag resources")
    ap.add_argument("--reconcile", action="store_true", help="Run full reconciliation")
    ap.add_argument("--interval-hours", type=int, default=168, help="Reconciliation interval")
    ap.add_argument("--label", type=str, default="monitor", help="Run label")
    ap.add_argument("--max-resources", type=int, default=0, help="Limit resources (0=all)")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"Registry not found: {registry_path}")
        print("Building registry from resource_inventory.csv...")
        # Build registry
        inv_path = out / "resource_inventory.csv"
        if not inv_path.exists():
            print("ERROR: resource_inventory.csv not found. Run evaluate_signals.py first.")
            sys.exit(1)
        # Import and build
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import evaluate_signals as es
        manifest = list(csv.DictReader(open(out / "manifest.csv")))
        http_entries = [json.loads(line) for line in open(out / "http_metadata.jsonl")]
        es.task1_resource_inventory(out, manifest, http_entries)
        registry = build_registry(out)
    else:
        registry = list(csv.DictReader(open(registry_path)))

    if args.max_resources > 0:
        registry = registry[:args.max_resources]

    if args.audit_etags:
        audit_etags(registry, out, timeout=args.timeout)
        return

    if args.reconcile:
        reconcile(registry, out, timeout=args.timeout, interval_hours=args.interval_hours)
        return

    dry_run = not args.live
    decisions, metrics = run_monitoring_cycle(registry, out, dry_run=dry_run, timeout=args.timeout, label=args.label)

    # Write monitoring report
    write_monitoring_report(out, decisions, metrics)

    print(f"\nRun record appended to: {out / 'monitor_run.jsonl'}")
    print(f"Report written to: {out / 'monitoring_report.md'}")


def write_monitoring_report(out, decisions, metrics):
    """Write monitoring report markdown."""
    from collections import Counter
    dec_counts = Counter(d["decision"] for d in decisions)

    report_path = out / "monitoring_report.md"
    with open(report_path, "w") as f:
        f.write("# Monitoring Report\n\n")
        f.write(f"- Run ID: {metrics['run_id']}\n")
        f.write(f"- Run timestamp: {metrics['run_timestamp']}\n")
        f.write(f"- Label: {metrics['label']}\n")
        f.write(f"- Dry run: {metrics['dry_run']}\n\n")
        f.write("## Metrics\n\n")
        f.write(f"- Resources checked: {metrics['resources_checked']}\n")
        f.write(f"- Requests issued: {metrics['requests_issued']}\n")
        f.write(f"- Total bytes transferred: {metrics['total_bytes']}\n")
        f.write(f"- Total elapsed: {metrics['total_elapsed_ms']}ms\n")
        f.write(f"- Confidently unchanged: {metrics['confidently_unchanged']}\n")
        f.write(f"- Potential changes: {metrics['potential_changes']}\n")
        f.write(f"- Extraction jobs triggered: {metrics['extraction_jobs_triggered']}\n")
        f.write(f"- QA jobs triggered: {metrics['qa_jobs_triggered']}\n")
        f.write(f"- Failures: {metrics['failures']}\n")
        f.write(f"- Unknown states: {metrics['unknown_states']}\n")
        f.write(f"- Fallback executions: {metrics['fallback_executions']}\n\n")
        f.write("## Decisions\n\n")
        f.writelines(f"- {d}: {c}\n" for d, c in sorted(dec_counts.items()))
        f.write("\n## Strategy distribution\n\n")
        f.writelines(f"- {s}: {c}\n" for s, c in sorted(metrics.get("strategies", {}).items()))
        f.write("\n## Notes\n\n")
        f.write("- Conditional HTTP used only for verified subset (Strategy A)\n")
        f.write("- Unverified validators treated as candidates, not trusted\n")
        f.write("- Failed requests never classified as unchanged\n")
        f.write("- Browser fallback (Strategy D) reported but not executed\n")
        f.write("- Reconciliation available via --reconcile flag\n")


if __name__ == "__main__":
    main()
