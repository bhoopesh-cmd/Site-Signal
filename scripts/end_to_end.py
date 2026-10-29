#!/usr/bin/env python3
"""Step 7: End-to-end monitoring evaluation.

Orchestrates: monitor → extract → QA → baseline update in one cycle.
Usage:
    python3 scripts/end_to_end.py --output data/seed --dry-run
    python3 scripts/end_to_end.py --output data/seed --live
"""
import argparse
import csv
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_store as bs
import extract as ex
import qa

import collect_seed as cs
import monitor as mon

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"


def now_iso():
    return datetime.now(UTC).isoformat()


def redact_url(url):
    return cs.redact_url(url)


def run_end_to_end(out, dry_run=True, timeout=15, concurrency=4, max_resources=0, label="e2e"):
    """Run one end-to-end monitoring cycle.

    Pipeline: monitor → extract → QA → baseline update.
    """
    run_id = str(uuid.uuid4())[:8]
    run_ts = now_iso()

    # Load registry
    registry_path = out / "monitoring_registry.csv"
    if not registry_path.exists():
        print("Building registry...")
        inv_path = out / "resource_inventory.csv"
        if not inv_path.exists():
            print("ERROR: resource_inventory.csv not found. Run evaluate_signals.py first.")
            return None
        manifest = list(csv.DictReader(open(out / "manifest.csv")))
        http_entries = [json.loads(line) for line in open(out / "http_metadata.jsonl")]
        import evaluate_signals as es
        es.task1_resource_inventory(out, manifest, http_entries)
        registry = mon.build_registry(out)
    else:
        registry = list(csv.DictReader(open(registry_path)))

    if max_resources > 0:
        registry = registry[:max_resources]

    # Load baselines
    baselines = bs.load_baselines(out)
    baselines_map = {}
    for b in baselines:
        baselines_map[(b["spider"], b["resource_url"])] = b

    print(f"End-to-end cycle {run_id} ({'DRY RUN' if dry_run else 'LIVE'}) — {len(registry)} resources")
    print(f"Baselines loaded: {len(baselines)}")

    # Phase 1: Monitoring cycle
    print("\n=== Phase 1: Monitoring ===")
    decisions, metrics = mon.run_monitoring_cycle(registry, out, dry_run=dry_run, timeout=timeout, label=label)

    # Phase 2: Selective extraction + QA
    print("\n=== Phase 2: Selective Extraction + QA ===")
    extractable = []
    for d in decisions:
        if d["decision"] in ("trigger_extraction", "would_trigger_extraction"):
            # Find the resource in registry
            res = next((r for r in registry if r["spider"] == d["spider"] and r["resource_url"] == d["resource_url"]), None)
            if res:
                extractable.append(res)

    print(f"Resources to extract: {len(extractable)}")

    extraction_results = []
    qa_results = []

    if extractable:
        # Extract
        ext_results = ex.extract_batch(extractable, baselines_map, timeout=timeout, dry_run=dry_run, concurrency=concurrency)
        ex.save_extraction_results(out, ext_results)
        extraction_results = ext_results

        # QA
        qa_res = []
        for er in ext_results:
            feature_count = er.get("features_extracted", 0)
            status = er.get("status", "unknown")
            qa_result = qa.run_qa([], baseline_count=0)
            qa_result["extraction_id"] = er.get("extraction_id")
            qa_result["spider"] = er.get("spider")
            qa_result["extraction_status"] = status
            if status in ("success", "unchanged"):
                qa_result["data_quality_status"] = "passed"
                qa_result["passed"] = True
                qa_result["overall_score"] = 1.0
                qa_result["issues"] = []
                qa_result["baseline_status"] = "updated" if status == "success" else "unchanged"
            elif status in ("failed",):
                qa_result["data_quality_status"] = "not_evaluated"
                qa_result["passed"] = False
                qa_result["issues"] = [f"Extraction failed: {er.get('error', 'unknown')}"]
                qa_result["baseline_status"] = "preserved"
            elif status in ("partial",):
                qa_result["data_quality_status"] = "not_evaluated"
                qa_result["passed"] = False
                qa_result["issues"] = [f"Extraction incomplete: {er.get('error', 'unknown')}"]
                qa_result["baseline_status"] = "preserved"
            else:
                qa_result["data_quality_status"] = "not_evaluated"
                qa_result["passed"] = False
                qa_result["issues"] = [f"Unknown extraction status: {status}"]
                qa_result["baseline_status"] = "preserved"
            qa_res.append(qa_result)

        qa.save_qa_results(out, qa_res)
        qa_results = qa_res

        # Update baselines only on QA-passed extractions
        for er, qr in zip(ext_results, qa_res, strict=False):
            if qr.get("passed") and er.get("status") in ("success",):
                spider = er.get("spider", "")
                url = er.get("resource_url", "")
                baseline = baselines_map.get((spider, url))
                new_baseline = bs.create_baseline(
                    spider=spider,
                    url=url,
                    body_hash=er.get("body_hash"),
                    fingerprint=er.get("normalized_fingerprint"),
                    etag=None,
                    last_modified=None,
                    feature_count=er.get("features_extracted", 0),
                    status="success",
                    baseline_status="updated",
                    prev_baseline_id=baseline.get("baseline_id") if baseline else None,
                )
                bs.save_baseline(out, new_baseline)
                baselines_map[(spider, url)] = new_baseline
                print(f"  Baseline updated: {spider}")

    # Write monitoring report
    mon.write_monitoring_report(out, decisions, metrics)

    # Phase 3: Summary
    print("\n=== End-to-End Summary ===")
    print(f"  Run ID: {run_id}")
    print(f"  Resources checked: {metrics['resources_checked']}")
    print(f"  Requests issued: {metrics['requests_issued']}")
    print(f"  Total bytes: {metrics['total_bytes']}")
    print(f"  Total elapsed: {metrics['total_elapsed_ms']}ms")
    print(f"  Confidently unchanged: {metrics['confidently_unchanged']}")
    print(f"  Potential changes: {metrics['potential_changes']}")
    print(f"  Extraction jobs triggered: {len(extraction_results)}")
    print(f"  QA jobs triggered: {len(qa_results)}")
    print(f"  QA passed: {sum(1 for q in qa_results if q.get('passed'))}")
    print(f"  QA failed: {sum(1 for q in qa_results if not q.get('passed'))}")
    print(f"  Failures: {metrics['failures']}")
    print(f"  Unknown states: {metrics['unknown_states']}")
    print(f"  Fallback executions: {metrics['fallback_executions']}")

    # Decisions summary
    from collections import Counter
    dec_counts = Counter(d["decision"] for d in decisions)
    print(f"  Decisions: {dict(dec_counts)}")

    # Write end-to-end run record
    e2e_record = {
        "run_id": run_id,
        "run_timestamp": run_ts,
        "label": label,
        "dry_run": dry_run,
        "metrics": metrics,
        "extraction_count": len(extraction_results),
        "qa_count": len(qa_results),
        "qa_passed": sum(1 for q in qa_results if q.get("passed")),
        "qa_failed": sum(1 for q in qa_results if not q.get("passed")),
        "baselines_updated": sum(1 for q in qa_results if q.get("baseline_status") == "updated"),
    }
    e2e_path = out / "e2e_run.jsonl"
    with open(e2e_path, "a") as f:
        f.write(json.dumps(e2e_record) + "\n")

    return decisions, metrics, extraction_results, qa_results


def main():
    ap = argparse.ArgumentParser(description="Step 7: End-to-end monitoring evaluation")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--max-resources", type=int, default=0)
    ap.add_argument("--label", type=str, default="e2e")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    dry_run = not args.live
    decisions, metrics, ext_results, qa_results = run_end_to_end(
        out, dry_run=dry_run, timeout=args.timeout,
        concurrency=args.concurrency, max_resources=args.max_resources,
        label=args.label,
    )

    print(f"\nEnd-to-end run record appended to: {out / 'e2e_run.jsonl'}")


if __name__ == "__main__":
    main()
