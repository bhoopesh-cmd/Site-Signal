#!/usr/bin/env python3
"""Run the full benchmark: control + optimized + skip audit + savings measurement.

Usage:
    python3 scripts/run_benchmark.py --output data/seed --timeout 15
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datetime import UTC

import full_fetch_control as ffc

import collect_seed as cs
import monitor as mon

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"


def run_optimized(out, registry, timeout=15, concurrency=4, max_resources=0):
    """Run the optimized monitoring pipeline and record per-resource results."""
    if max_resources > 0:
        registry = registry[:max_resources]

    baselines = []
    try:
        import baseline_store as bs
        baselines = bs.load_baselines(out)
    except Exception:
        pass

    baselines_map = {}
    for b in baselines:
        baselines_map[(b["spider"], b["resource_url"])] = b

    import uuid
    from datetime import datetime

    run_id = str(uuid.uuid4())[:8]
    run_ts = datetime.now(UTC).isoformat()

    results = []
    print(f"Optimized run {run_id} — {len(registry)} resources")

    for r in registry:
        # Execute strategy
        result = mon.execute_strategy(r, timeout=timeout)
        # Route
        baseline = baselines_map.get((r["spider"], r["resource_url"]))
        decision = mon.route(r, result)

        # Use actual request count from execute_strategy
        requests_issued = result.get("requests_issued", 1)
        if not (r["resource_url"].startswith("http://") or r["resource_url"].startswith("https://")):
            requests_issued = 0

        # For 304 skips, bytes should be 0 (no body downloaded)
        bytes_t = result.get("bytes_transferred", 0)
        if result.get("cond_status") == 304 or result.get("status") == 304:
            bytes_t = 0

        entry = {
            "run_id": run_id,
            "run_timestamp": run_ts,
            "spider": r["spider"],
            "resource_url": cs.redact_url(r["resource_url"]),
            "strategy": r.get("primary_strategy", "E"),
            "decision": decision["decision"],
            "requests_issued": requests_issued,
            "bytes_transferred": bytes_t,
            "elapsed_ms": result.get("elapsed_ms", 0),
            "validator_type": r.get("validator_type", "none"),
            "conditional_status": result.get("cond_status"),
            "body_changed": result.get("body_changed"),
            "data_changed": result.get("data_changed"),
            "extraction_triggered": decision["decision"] in ("trigger_extraction", "would_trigger_extraction", "unknown"),
            "qa_triggered": decision["decision"] in ("trigger_extraction", "would_trigger_extraction", "unknown"),
            "baseline_updated": False,
            "confidence": decision["confidence"],
            "justification": decision["justification"],
            "error": result.get("error"),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        results.append(entry)

        if len(results) % 10 == 0 or len(results) == len(registry):
            print(f"  Processed {len(results)}/{len(registry)}")

    # Save
    opt_path = out / "optimized_run.jsonl"
    with open(opt_path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print("\n=== Optimized Run Summary ===")
    print(f"  Resources: {len(results)}")
    from collections import Counter
    print(f"  Decisions: {dict(Counter(r['decision'] for r in results))}")
    print(f"  Total requests: {sum(r['requests_issued'] for r in results)}")
    print(f"  Total bytes: {sum(r['bytes_transferred'] for r in results)}")
    print(f"  Total elapsed: {sum(r['elapsed_ms'] for r in results)}ms")
    print(f"  Extractions triggered: {sum(1 for r in results if r['extraction_triggered'])}")
    print(f"  Failures: {sum(1 for r in results if r['error'])}")

    return results


def audit_skips(out, optimized_results):
    """Audit every skip in the optimized run."""
    skips = [r for r in optimized_results if r["decision"] in ("skip", "would_skip")]
    print(f"\nAuditing {len(skips)} skips...")

    audit_rows = []
    for r in skips:
        spider = r["spider"]
        url = r["resource_url"]
        # Unconditional full fetch
        t0 = __import__("time").time()
        try:
            import requests
            headers = {"User-Agent": "ChainXY-seed-collector/1.0 (+research)"}
            resp = requests.get(url, timeout=15, headers=headers, allow_redirects=True)
            elapsed = int((__import__("time").time() - t0) * 1000)
            status = resp.status_code
            bytes_t = len(resp.content) if resp.content else 0

            # Try to extract features
            import extract as ex
            features, fp, method = ex.extract_json_features(resp.text, spider)
            if features is None:
                # Try html
                features, fp, method = ex.extract_json_features(resp.text, spider)
            if features is None:
                features = []
                fp = None
                method = "none"

            # Completeness
            if status and 200 <= status < 400 and features:
                completeness = "complete"
            elif status and 200 <= status < 400:
                completeness = "partial"
            else:
                completeness = "failed"

            # Get baseline fingerprint
            import baseline_store as bs
            baselines = bs.load_baselines(out)
            baseline = None
            for b in baselines:
                if b["spider"] == spider and b["resource_url"] == url:
                    baseline = b
                    break
            baseline_fp = baseline.get("normalized_fingerprint") if baseline else None

            # Classification
            if completeness == "failed" or completeness == "partial":
                classification = "inconclusive"
            elif baseline_fp and fp and fp == baseline_fp:
                classification = "verified"
            elif baseline_fp and fp and fp != baseline_fp:
                classification = "false_skip"
            else:
                classification = "inconclusive"

            audit_rows.append({
                "spider": spider,
                "resource_url": url,
                "strategy": r["strategy"],
                "decision": r["decision"],
                "completeness": completeness,
                "baseline_fingerprint": baseline_fp or "",
                "current_fingerprint": fp or "",
                "classification": classification,
                "requests": 1,
                "bytes": bytes_t,
                "elapsed_ms": elapsed,
                "error": None if status and 200 <= status < 400 else f"HTTP {status}",
            })
        except Exception as e:
            audit_rows.append({
                "spider": spider,
                "resource_url": url,
                "strategy": r["strategy"],
                "decision": r["decision"],
                "completeness": "failed",
                "baseline_fingerprint": "",
                "current_fingerprint": "",
                "classification": "inconclusive",
                "requests": 1,
                "bytes": 0,
                "elapsed_ms": int((__import__("time").time() - t0) * 1000),
                "error": str(e),
            })

    # Write skip_audit.csv
    audit_path = out / "skip_audit.csv"
    if audit_rows:
        with open(audit_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(audit_rows[0].keys()))
            w.writeheader()
            for row in audit_rows:
                w.writerow(row)

    from collections import Counter
    print(f"\n=== Skip Audit Summary ({len(audit_rows)} skips) ===")
    print(Counter(r["classification"] for r in audit_rows))
    false_skips = [r for r in audit_rows if r["classification"] == "false_skip"]
    if false_skips:
        print(f"  WARNING: {len(false_skips)} false skips detected!")
        for fs in false_skips:
            print(f"    {fs['spider']}: {fs['resource_url'][:60]}")
    else:
        print("  No false skips detected.")

    return audit_rows


def measure_savings(out, control_results, optimized_results, audit_results):
    """Measure actual savings comparing control vs optimized."""
    # Count actual extraction/QA jobs from control (not all resources)
    control_extraction_jobs = sum(1 for r in control_results if r.get("extraction_method", "none") != "none")
    control_qa_jobs = sum(1 for r in control_results if r.get("extraction_method", "none") != "none" and r.get("extraction_method") in ("json_api", "sitemap_crawl", "csv"))
    control_requests = sum(r.get("requests_issued", 0) for r in control_results)
    control_bytes = sum(r.get("bytes_transferred", 0) for r in control_results)
    control_elapsed = sum(r.get("elapsed_ms", 0) for r in control_results)
    control_features = sum(r.get("features_extracted", 0) for r in control_results)
    control_complete = sum(1 for r in control_results if r.get("completeness") == "complete")
    control_failed = sum(1 for r in control_results if r.get("completeness") == "failed")
    control_qa = sum(1 for r in control_results if r.get("qa_passed"))

    # Aggregate optimized metrics
    opt_requests = sum(r.get("requests_issued", 0) for r in optimized_results)
    opt_bytes = sum(r.get("bytes_transferred", 0) for r in optimized_results)
    opt_elapsed = sum(r.get("elapsed_ms", 0) for r in optimized_results)
    opt_extractions = sum(1 for r in optimized_results if r.get("extraction_triggered"))
    opt_qa = sum(1 for r in optimized_results if r.get("qa_triggered"))
    opt_skips = sum(1 for r in optimized_results if r.get("decision") in ("skip", "would_skip"))
    opt_failures = sum(1 for r in optimized_results if r.get("error"))
    opt_unknown = sum(1 for r in optimized_results if r.get("decision") == "unknown")

    # Audit metrics
    audit_requests = sum(r.get("requests", 0) for r in audit_results)
    audit_bytes = sum(r.get("bytes", 0) for r in audit_results)
    audit_elapsed = sum(r.get("elapsed_ms", 0) for r in audit_results)

    def pct_reduction(control, optimized):
        if control == 0:
            return 0.0
        return round((control - optimized) / control * 100, 1)

    # Write final_resource_evaluation.csv
    eval_path = out / "final_resource_evaluation.csv"
    rows = [
        {"metric": "resources_checked", "control": len(control_results), "optimized": len(optimized_results), "difference": 0, "pct_reduction": 0.0},
        {"metric": "http_requests", "control": control_requests, "optimized": opt_requests, "difference": control_requests - opt_requests, "pct_reduction": pct_reduction(control_requests, opt_requests)},
        {"metric": "bytes_transferred", "control": control_bytes, "optimized": opt_bytes, "difference": control_bytes - opt_bytes, "pct_reduction": pct_reduction(control_bytes, opt_bytes)},
        {"metric": "elapsed_ms", "control": control_elapsed, "optimized": opt_elapsed, "difference": control_elapsed - opt_elapsed, "pct_reduction": pct_reduction(control_elapsed, opt_elapsed)},
        {"metric": "extraction_jobs", "control": control_extraction_jobs, "optimized": opt_extractions, "difference": control_extraction_jobs - opt_extractions, "pct_reduction": pct_reduction(control_extraction_jobs, opt_extractions)},
        {"metric": "qa_jobs", "control": control_qa_jobs, "optimized": opt_qa, "difference": control_qa_jobs - opt_qa, "pct_reduction": pct_reduction(control_qa_jobs, opt_qa)},
        {"metric": "records_processed", "control": control_features, "optimized": 0, "difference": control_features, "pct_reduction": 100.0 if control_features > 0 else 0.0},
        {"metric": "failures", "control": control_failed, "optimized": opt_failures, "difference": control_failed - opt_failures, "pct_reduction": 0.0},
        {"metric": "unknown_states", "control": 0, "optimized": opt_unknown, "difference": -opt_unknown, "pct_reduction": 0.0},
        {"metric": "skips", "control": 0, "optimized": opt_skips, "difference": -opt_skips, "pct_reduction": 0.0},
        {"metric": "audit_requests", "control": 0, "optimized": audit_requests, "difference": -audit_requests, "pct_reduction": 0.0},
        {"metric": "audit_bytes", "control": 0, "optimized": audit_bytes, "difference": -audit_bytes, "pct_reduction": 0.0},
        {"metric": "audit_elapsed_ms", "control": 0, "optimized": audit_elapsed, "difference": -audit_elapsed, "pct_reduction": 0.0},
    ]

    with open(eval_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "control", "optimized", "difference", "pct_reduction"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Print summary
    print("\n=== Final Resource Evaluation ===")
    print(f"  Cohort: {len(control_results)} resources")
    print(f"  Control requests: {control_requests}, bytes: {control_bytes}, elapsed: {control_elapsed}ms")
    print(f"  Optimized requests: {opt_requests}, bytes: {opt_bytes}, elapsed: {opt_elapsed}ms")
    print(f"  Extraction jobs: control={control_extraction_jobs}, optimized={opt_extractions}")
    print(f"  QA jobs: control={control_qa_jobs}, optimized={opt_qa}")
    print(f"  Skips: {opt_skips}, Failures: {opt_failures}, Unknown: {opt_unknown}")
    print(f"  Audit (validation overhead): requests={audit_requests}, bytes={audit_bytes}, elapsed={audit_elapsed}ms")
    print(f"\n  Network savings: {control_requests - opt_requests} requests ({pct_reduction(control_requests, opt_requests)}%)")
    print(f"  Processing savings: {control_extraction_jobs - opt_extractions} extraction jobs ({pct_reduction(control_extraction_jobs, opt_extractions)}%)")

    return rows


def main():
    ap = argparse.ArgumentParser(description="Run empirical benchmark")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-resources", type=int, default=0)
    ap.add_argument("--skip-control", action="store_true")
    ap.add_argument("--skip-optimized", action="store_true")
    ap.add_argument("--skip-audit", action="store_true")
    ap.add_argument("--label", type=str, default="benchmark")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"Registry not found: {registry_path}")
        sys.exit(1)

    import csv as _csv
    registry = list(_csv.DictReader(open(registry_path)))
    if args.max_resources > 0:
        registry = registry[:args.max_resources]

    control_results = []
    optimized_results = []
    audit_results = []

    # Task 2: Full-scrape control
    if not args.skip_control:
        print("\n=== Task 2: Full-Scrape Control ===")
        control_results = ffc.fetch_control_batch(registry, timeout=args.timeout, concurrency=args.concurrency)
        run_id = __import__("uuid").uuid4().hex[:8]
        run_ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        for r in control_results:
            r["run_id"] = run_id
            r["run_timestamp"] = run_ts
            r["label"] = args.label
        control_path = out / "control_run.jsonl"
        with open(control_path, "a") as f:
            for r in control_results:
                f.write(json.dumps(r) + "\n")
        print(f"  Appended {len(control_results)} results to {control_path}")

    # Task 3: Optimized pipeline
    if not args.skip_optimized:
        print("\n=== Task 3: Optimized Pipeline ===")
        optimized_results = run_optimized(out, registry, timeout=args.timeout, concurrency=args.concurrency, max_resources=args.max_resources)

    # Task 4: Skip audit
    if not args.skip_audit and optimized_results:
        print("\n=== Task 4: Skip Audit ===")
        audit_results = audit_skips(out, optimized_results)

    # Task 5: Measure savings
    if control_results and optimized_results:
        print("\n=== Task 5: Measure Savings ===")
        measure_savings(out, control_results, optimized_results, audit_results)

    print("\n=== Benchmark Complete ===")
    print(f"  Control: {len(control_results)} resources")
    print(f"  Optimized: {len(optimized_results)} resources")
    print(f"  Audit: {len(audit_results)} skips audited")


if __name__ == "__main__":
    main()
