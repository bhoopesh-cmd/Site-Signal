#!/usr/bin/env python3
"""Site-Signal monitoring engine.

Strategies:
  A: Conditional HTTP (ETag/Last-Modified)
  B: Direct payload comparison
  C: Alternative metadata/feed
  D: Browser fallback
  E: Unresolved
"""
import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store as bs

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
TOKEN_RE = None


def redact_url(url):
    import re
    if not url:
        return url
    return re.sub(r"(token|key|api_key|apikey|secret|password|auth)=[^&]+", r"\1=REDACTED", url, flags=re.IGNORECASE)


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

    accessible = False
    if isinstance(status, int):
        accessible = 200 <= status < 400
    elif isinstance(status, str) and status.isdigit():
        accessible = 200 <= int(status) < 400

    has_validator = bool(etag or lm)
    if contains_loc == "verified" and has_validator and accessible:
        if cond_works == "yes":
            return "A", "candidate", "Verified location data + working conditional request", "B", "Validator reliability unverified (single observation)"
        else:
            return "A", "candidate", "Verified location data + validator present (conditional untested)", "B", "Conditional request not yet verified"

    if resource_type in ("XML",) and "sitemap" in (source_url or "").lower():
        if accessible:
            return "C", "candidate", "Sitemap/feed identified and accessible", "B", "Sitemap change-detection unverified"

    if rendering == "js" and (not source_url or not accessible):
        return "D", "low", "JS-rendered source, no accessible direct URL", "E", "Browser execution not available in prototype"

    if contains_loc == "verified" and accessible:
        return "B", "possible", "Verified location data, no usable validator, accessible", "D", "No conditional request possible"

    if not accessible or not source_url:
        return "E", "low", "Inaccessible or insufficiently understood", "D", "Source inaccessible or ambiguous"

    if accessible:
        return "B", "low", "Accessible but location data unverified", "D", "Location-data relevance unverified"

    return "E", "low", "Insufficient evidence", "D", "Unresolved"


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
        "requests_issued": 0,
    }

    t0 = time.time()
    try:
        # Strategy A: Conditional-first approach
        # If we have a stored baseline ETag/LM, try conditional request first
        # to avoid downloading the full body when unchanged.
        stored_etag = resource.get("etag", "") or (baseline.get("etag") if baseline else "")
        stored_lm = resource.get("last_modified", "") or (baseline.get("last_modified") if baseline else "")

        if strategy == "A" and (stored_etag or stored_lm):
            # Conditional-first: try If-None-Match / If-Modified-Since
            cond_headers = {"User-Agent": USER_AGENT}
            if stored_etag:
                cond_headers["If-None-Match"] = stored_etag
            if stored_lm:
                cond_headers["If-Modified-Since"] = stored_lm

            t1 = time.time()
            try:
                cr = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)
                result["requests_issued"] += 1
                result["cond_status"] = cr.status_code
                result["cond_elapsed_ms"] = int((time.time() - t1) * 1000)
                result["cond_bytes"] = len(cr.content) if cr.content else 0

                if cr.status_code == 304:
                    # Unchanged — no body downloaded
                    result["status"] = 304
                    result["etag"] = stored_etag
                    result["last_modified"] = stored_lm
                    result["body_changed"] = False
                    result["data_changed"] = False
                    result["validator_changed"] = False
                    result["bytes_transferred"] = 0
                    result["elapsed_ms"] = int((time.time() - t0) * 1000)
                    if baseline:
                        result["body_hash"] = baseline.get("body_hash")
                        result["normalized_fingerprint"] = baseline.get("normalized_fingerprint")
                    return result

                # 200: changed — we have the body
                result["status"] = cr.status_code
                result["etag"] = cr.headers.get("ETag") or stored_etag
                result["last_modified"] = cr.headers.get("Last-Modified") or stored_lm
                result["bytes_transferred"] = len(cr.content) if cr.content else 0
                r = cr  # use conditional response as the main response
                result["validator_changed"] = True

                if cr.content:
                    new_hash = hashlib.sha256(cr.content).hexdigest()
                    result["body_hash"] = new_hash
                    if baseline and baseline.get("body_hash"):
                        result["body_changed"] = (new_hash != baseline["body_hash"])
                    # Try to extract location features
                    try:
                        import baseline_store as bs
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
                result["elapsed_ms"] = int((time.time() - t0) * 1000)
                return result

            except Exception as e:
                result["error"] = str(e)
                result["failed"] = True
                result["elapsed_ms"] = int((time.time() - t0) * 1000)
                return result

        # Default: unconditional fetch (for all other strategies and Strategy A without baseline)
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            r = requests.post(url, timeout=timeout, headers=headers, allow_redirects=True)
        else:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)

        result["requests_issued"] += 1
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["status"] = r.status_code
        result["etag"] = r.headers.get("ETag")
        result["last_modified"] = r.headers.get("Last-Modified")
        result["bytes_transferred"] = len(r.content) if r.content else 0

        # Handle 304 Not Modified
        if r.status_code == 304:
            if baseline:
                result["body_hash"] = baseline.get("body_hash")
                result["normalized_fingerprint"] = baseline.get("normalized_fingerprint")
                result["body_changed"] = False
                result["data_changed"] = False
                result["validator_changed"] = False
                result["status"] = 304
            else:
                result["body_hash"] = None
                result["normalized_fingerprint"] = None
                result["body_changed"] = "unknown"
                result["data_changed"] = "unknown"
                result["validator_changed"] = False
                result["status"] = 304
                result["error"] = "304 without saved baseline"
                result["failed"] = True
            return result

        if not (200 <= r.status_code < 400):
            result["failed"] = True
            result["error"] = f"HTTP {r.status_code}"
            return result

        # Strategy A without baseline: do conditional request (uses fresh ETag)
        if strategy == "A":
            etag = resource.get("etag", "") or r.headers.get("ETag", "")
            lm = resource.get("last_modified", "") or r.headers.get("Last-Modified", "")
            if etag:
                cond_headers = {"User-Agent": USER_AGENT, "If-None-Match": etag}
                t1 = time.time()
                try:
                    cr = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)
                    result["requests_issued"] += 1
                    result["cond_status"] = cr.status_code
                    result["cond_elapsed_ms"] = int((time.time() - t1) * 1000)
                    result["cond_bytes"] = len(cr.content) if cr.content else 0
                    if cr.status_code == 304:
                        result["validator_changed"] = False
                        result["body_changed"] = False
                        result["data_changed"] = False
                        if baseline:
                            result["body_hash"] = baseline.get("body_hash")
                            result["normalized_fingerprint"] = baseline.get("normalized_fingerprint")
                    elif cr.status_code == 200:
                        result["validator_changed"] = True
                        if cr.content:
                            new_hash = hashlib.sha256(cr.content).hexdigest()
                            result["body_hash"] = new_hash
                            if baseline and baseline.get("body_hash"):
                                result["body_changed"] = (new_hash != baseline["body_hash"])
                            try:
                                import baseline_store as bs
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
                    result["requests_issued"] += 1
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

        # Strategy B/C: Direct payload
        if strategy in ("B", "C"):
            result["body_hash"] = hashlib.sha256(r.content).hexdigest() if r.content else None
            try:
                import baseline_store as bs
                features, fp = bs.extract_location_features(r.text, resource.get("spider", ""))
                if fp:
                    result["normalized_fingerprint"] = fp
                    result["fingerprint_method"] = "extracted"
                else:
                    result["normalized_fingerprint"] = result["body_hash"]
                    result["fingerprint_method"] = "body_hash_proxy"
            except Exception:
                result["normalized_fingerprint"] = result.get("body_hash")
                result["fingerprint_method"] = "body_hash_proxy"
            if baseline and baseline.get("body_hash"):
                result["body_changed"] = (result["body_hash"] != baseline["body_hash"])
            if baseline and baseline.get("normalized_fingerprint") and result.get("normalized_fingerprint"):
                result["data_changed"] = (result["normalized_fingerprint"] != baseline["normalized_fingerprint"])

    except Exception as e:
        result["error"] = str(e)
        result["failed"] = True
        result["elapsed_ms"] = int((time.time() - t0) * 1000)

    return result


def route(resource, result):
    """Route a monitoring result to a decision."""
    strategy = resource.get("primary_strategy", "E")
    status = result.get("status")
    body_changed = result.get("body_changed")
    data_changed = result.get("data_changed")
    failed = result.get("failed", False)
    error = result.get("error")

    if failed or error:
        return {
            "decision": "unknown",
            "justification": f"Request error: {error}" if error else "Request failed",
            "confidence": "n/a",
            "fallback_executed": True,
        }

    if strategy == "A":
        cond_status = result.get("cond_status")
        if cond_status == 304 or status == 304:
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
            return {
                "decision": "unknown",
                "justification": "Conditional request returned 200 but data change unknown; needs verification",
                "confidence": "low",
                "fallback_executed": True,
            }
        return {
            "decision": "unknown",
            "justification": f"Unexpected status {status}/{cond_status} for conditional HTTP",
            "confidence": "n/a",
            "fallback_executed": True,
        }

    if strategy == "B":
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
        md_changed = result.get("metadata_changed")
        if md_changed is True:
            return {
                "decision": "trigger_extraction",
                "justification": "Metadata/feed change detected",
                "confidence": "low",
                "fallback_executed": False,
            }
        if md_changed is False:
            return {
                "decision": "skip",
                "justification": "No metadata/feed change detected",
                "confidence": "low",
                "fallback_executed": False,
            }
        return {
            "decision": "unknown",
            "justification": "Metadata check inconclusive or failed",
            "confidence": "low",
            "fallback_executed": True,
        }

    if strategy == "D":
        return {
            "decision": "fallback_needed",
            "justification": "Browser rendering required; direct endpoint not available",
            "confidence": "n/a",
            "fallback_executed": False,
        }

    # Strategy E: unresolved
    return {
        "decision": "unknown",
        "justification": "Unresolved: no viable monitoring signal",
        "confidence": "n/a",
        "fallback_executed": True,
    }


def run_monitoring_cycle(registry, out=None, timeout=15, dry_run=False, label="monitor"):
    """Run a full monitoring cycle over all registry resources."""
    import uuid
    run_id = str(uuid.uuid4())[:8]
    run_ts = datetime.now(UTC).isoformat()
    baselines = bs.load_baselines(out if out else Path("data/seed"))
    print(f"Loaded {len(baselines)} existing baselines")

    decisions = []
    changes = []
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
        metrics["requests_issued"] += result.get("requests_issued", 1)
        metrics["total_bytes"] += result.get("bytes_transferred", 0)
        metrics["total_elapsed_ms"] += result.get("elapsed_ms", 0)

        # Compare with baseline
        comparison = bs.compare_baselines(baseline, {
            "body_hash": result.get("body_hash"),
            "normalized_fingerprint": result.get("normalized_fingerprint"),
            "status": result.get("status"),
            "etag": result.get("etag"),
            "last_modified": result.get("last_modified"),
        })

        # Overwrite data_changed with comparison result if available
        if comparison.get("data_changed") is not None:
            result["data_changed"] = comparison["data_changed"]
        if comparison.get("body_changed") is not None:
            result["body_changed"] = comparison["body_changed"]
        if comparison.get("validator_changed") is not None:
            result["validator_changed"] = comparison["validator_changed"]

        # Route
        decision = route(r, result)
        entry = {
            "spider": r["spider"],
            "resource_url": redact_url(r["resource_url"]),
            "strategy": strategy,
            "decision": decision["decision"],
            "justification": decision["justification"],
            "confidence": decision["confidence"],
            "status": result.get("status"),
            "cond_status": result.get("cond_status"),
            "body_changed": result.get("body_changed"),
            "data_changed": result.get("data_changed"),
            "validator_changed": result.get("validator_changed"),
            "requests_issued": result.get("requests_issued", 1),
            "bytes_transferred": result.get("bytes_transferred", 0),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "error": result.get("error"),
        }
        decisions.append(entry)

        if decision["decision"] in ("trigger_extraction", "would_trigger_extraction"):
            metrics["potential_changes"] += 1
        elif decision["decision"] in ("skip", "would_skip"):
            metrics["confidently_unchanged"] += 1
        elif decision["decision"] == "unknown":
            metrics["unknown_states"] += 1

        if result.get("failed") or result.get("error"):
            metrics["failures"] += 1

        if decision["decision"] == "fallback_needed":
            metrics["fallback_executions"] += 1

        if decision["decision"] in ("trigger_extraction", "would_trigger_extraction"):
            metrics["extraction_jobs_triggered"] += 1
            metrics["qa_jobs_triggered"] += 1

        # Save baseline (append-only) — only if eligible
        import baseline_store as bs2
        eligible, reason = bs2.is_baseline_eligible(result, has_existing_baseline=baseline is not None)
        if eligible:
            new_baseline = bs2.create_baseline(
                spider=r["spider"],
                url=r["resource_url"],
                body_hash=result.get("body_hash"),
                fingerprint=result.get("normalized_fingerprint"),
                status=result.get("status"),
                etag=result.get("etag"),
                last_modified=result.get("last_modified"),
                feature_count=0,
                prev_baseline_id=baseline.get("baseline_id") if baseline else None,
            )
            baselines.append(new_baseline)

        # Record detected change
        if result.get("data_changed") is True or result.get("body_changed") is True:
            changes.append({
                "spider": r["spider"],
                "resource_url": redact_url(r["resource_url"]),
                "strategy": strategy,
                "detection_method": strategy,
                "body_changed": result.get("body_changed"),
                "data_changed": result.get("data_changed"),
                "validator_changed": result.get("validator_changed"),
                "timestamp": datetime.now(UTC).isoformat(),
            })

    # Write outputs
    if out is None:
        out = Path("data/seed")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    # Append monitor run
    run_record = {
        "run_id": run_id,
        "run_timestamp": run_ts,
        "label": label,
        "dry_run": dry_run,
        "metrics": metrics,
        "decisions": decisions,
    }
    with open(out / "monitor_run.jsonl", "a") as f:
        f.write(json.dumps(run_record) + "\n")

    # Append changes
    if changes:
        import csv as _csv
        with open(out / "monitoring_changes.csv", "a", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["spider", "resource_url", "strategy", "detection_method",
                                               "body_changed", "data_changed", "validator_changed", "timestamp"])
            # Write header only if file is empty
            if f.tell() == 0:
                w.writeheader()
            for c in changes:
                w.writerow(c)

    # Write report
    report_path = out / "monitoring_report.md"
    with open(report_path, "w") as f:
        f.write(f"# Monitoring Report — {run_id}\n\n")
        f.write(f"- Timestamp: {run_ts}\n")
        f.write(f"- Label: {label}\n")
        f.write(f"- Dry run: {dry_run}\n")
        f.write(f"- Resources checked: {metrics['resources_checked'] if 'resources_checked' in metrics else len(registry)}\n")
        f.write(f"- Resources checked: {len(registry)}\n")
        f.write(f"- Requests issued: {metrics['requests_issued']}\n")
        f.write(f"- Total bytes: {metrics['total_bytes']}\n")
        f.write(f"- Total elapsed: {metrics['total_elapsed_ms']}ms\n")
        f.write(f"- Confidently unchanged: {metrics['confidently_unchanged']}\n")
        f.write(f"- Potential changes: {metrics['potential_changes']}\n")
        f.write(f"- Extraction jobs triggered: {metrics['extraction_jobs_triggered']}\n")
        f.write(f"- QA jobs triggered: {metrics['qa_jobs_triggered']}\n")
        f.write(f"- Failures: {metrics['failures']}\n")
        f.write(f"- Unknown states: {metrics['unknown_states']}\n")
        f.write(f"- Fallback executions: {metrics['fallback_executions']}\n")
        f.write(f"- Decisions: {json.dumps({d['decision']: sum(1 for x in decisions if x['decision'] == d['decision']) for d in decisions}, indent=2)}\n")

    print("\n=== Monitoring Cycle Summary ===")
    print(f"  Resources checked: {len(registry)}")
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
    print(f"  Decisions: {dict(Counter(d['decision'] for d in decisions))}")
    return decisions, metrics


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
        f.write(f"- Resources checked: {metrics['resources_checked'] if 'resources_checked' in metrics else len(decisions)}\n")
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


def reconcile(registry, timeout=15):
    """Run reconciliation: full independent check on all resources."""
    print("Running reconciliation (full independent check)...")
    baselines = bs.load_baselines(Path("data/seed"))
    decisions = []
    for r in registry:
        baseline = bs.get_latest_baseline(baselines, r["spider"], r["resource_url"])
        # Always compare payload (treat as Strategy B)
        result = execute_strategy(r, timeout=timeout, baseline=baseline)
        # Force strategy to B for reconciliation
        result["strategy"] = "reconciliation"
        decision = route(r, result)
        decisions.append({
            "spider": r["spider"],
            "resource_url": redact_url(r["resource_url"]),
            "decision": decision["decision"],
            "justification": decision["justification"],
        })
    print(f"Reconciliation complete: {len(decisions)} resources checked")
    return decisions


def audit_etags(registry, timeout=15):
    """Audit ETag reliability across all resources with validators."""
    print("Auditing ETags...")
    results = []
    for r in registry:
        if r.get("primary_strategy") != "A":
            continue
        url = r["resource_url"]
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        try:
            headers = {"User-Agent": USER_AGENT}
            r1 = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
            etag = r1.headers.get("ETag")
            lm = r1.headers.get("Last-Modified")

            # Conditional request
            cond_headers = {"User-Agent": USER_AGENT}
            if etag:
                cond_headers["If-None-Match"] = etag
            elif lm:
                cond_headers["If-Modified-Since"] = lm

            r2 = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)

            etag_strength = "strong" if (etag and not etag.startswith("W/")) else ("weak" if etag else "none")
            result = {
                "spider": r["spider"],
                "resource_url": redact_url(url),
                "etag": etag,
                "last_modified": lm,
                "etag_strength": etag_strength,
                "initial_status": r1.status_code,
                "conditional_status": r2.status_code,
                "conditional_honored": r2.status_code == 304,
                "classification": "correct_negative_candidate" if r2.status_code == 304 else "requires_verification",
                "notes": "304 returned; conditional honored. Location data unchanged in this observation." if r2.status_code == 304 else "200 returned; validator changed or ignored.",
            }
            results.append(result)
        except Exception as e:
            results.append({
                "spider": r["spider"],
                "resource_url": redact_url(url),
                "etag": None,
                "last_modified": None,
                "etag_strength": "none",
                "initial_status": None,
                "conditional_status": None,
                "conditional_honored": False,
                "classification": "error",
                "notes": str(e),
            })

    # Write etag_audit.csv
    out = Path("data/seed")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "etag_audit.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["spider", "resource_url", "etag", "last_modified",
                                          "etag_strength", "initial_status", "conditional_status",
                                          "conditional_honored", "classification", "notes"])
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"ETag audit complete: {len(results)} resources audited")
    honored = sum(1 for r in results if r["conditional_honored"])
    print(f"  Conditional honored: {honored}/{len(results)}")
    return results


def build_registry(csv_path):
    """Build monitoring registry from resource inventory CSV."""
    registry = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            registry.append({
                "spider": row["spider"],
                "brand": row.get("brand", ""),
                "domain": row.get("domain", ""),
                "resource_url": row["original_url"],
                "http_method": row.get("http_method", "GET"),
                "resource_type": row.get("resource_type", ""),
                "primary_strategy": row["primary_strategy"],
                "strategy_name": row.get("strategy_name", ""),
                "validator_type": row.get("validator_type", "none"),
                "validator_availability": row.get("validator_availability", "none"),
                "etag": row.get("etag", ""),
                "last_modified": row.get("last_modified", ""),
                "etag_strength": row.get("etag_strength", "none"),
                "location_data_verified": row.get("location_data_verified", "unknown"),
                "confidence": row.get("confidence", "low"),
                "evidence": row.get("evidence", ""),
                "last_successful_observation": row.get("last_successful_observation", ""),
                "fallback_method": row.get("fallback_method", ""),
                "unresolved_limitations": row.get("unresolved_limitations", ""),
                "feature_count": row.get("feature_count", "0"),
                "probe_status": row.get("probe_status", ""),
                "conditional_works": row.get("conditional_works", ""),
                "monitoring_resource": row.get("monitoring_resource", ""),
                "monitoring_resource_confidence": row.get("monitoring_resource_confidence", ""),
            })
    return registry


def main():
    ap = argparse.ArgumentParser(description="Site-Signal monitoring engine")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-resources", type=int, default=0)
    ap.add_argument("--label", type=str, default="monitor")
    ap.add_argument("--audit-etags", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    args = ap.parse_args()

    registry = build_registry(args.registry)
    if args.max_resources > 0:
        registry = registry[:args.max_resources]

    if args.audit_etags:
        audit_etags(registry, args.timeout)
        return

    if args.reconcile:
        reconcile(registry, args.timeout)
        return

    metrics = run_monitoring_cycle(registry, timeout=args.timeout, dry_run=args.dry_run, label=args.label)
    print(f"\nDone. Metrics: {json.dumps(metrics, indent=2)}")


if __name__ == "__main__":
    main()
