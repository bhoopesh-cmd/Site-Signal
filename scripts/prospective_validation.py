#!/usr/bin/env python3
"""Synchronized prospective validation of Site-Signal.

Phases:
  baseline  — freeze Data B (an AllThePlaces run) location fingerprints + provenance
  monitor   — capture live Site-Signal signals now (ETag/Last-Modified/body hash/
              stable location fingerprint) for the eligible cohort
  freeze    — record Site-Signal predictions (unchanged/changed/unknown/failed)
              BEFORE Data A is inspected
  evaluate  — compare Data A vs Data B (stable fingerprint) and score predictions

Design notes:
- Ground truth is AllThePlaces published per-spider data only; we never re-scrape
  to validate.
- Predictions are append-only and timestamped; a failed/unknown result is never a skip.
- A coverage guard marks resources unknown when the live extraction covers <70% of
  the baseline feature count (incomplete coverage is not a change).

Usage:
  # 1) build synchronized baseline from the latest run + capture live signals + freeze
  python3 scripts/prospective_validation.py baseline --run-b 2026-10-17-13-32-31
  python3 scripts/prospective_validation.py monitor  --run-b 2026-10-17-13-32-31
  python3 scripts/prospective_validation.py evaluate --run-b 2026-10-17-13-32-31 --run-a <next>
"""
import argparse
import csv
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as ex
import validate_two_runs as v2

UA = "ChainXY-seed-collector/1.0 (+research)"
DATA_BASE = "https://alltheplaces-data.openaddresses.io/runs"
COVERAGE_THRESHOLD = 0.7


def now_iso():
    return datetime.now(UTC).isoformat()


def classify_prediction(any_ok, live_count, baseline_count, live_fp, baseline_fp):
    """Pure prediction classifier. Returns (decision, reason).

    A failed/unknown result is never a skip. Incomplete coverage is unknown.
    """
    if not any_ok or live_count == 0:
        return ("failed" if any_ok else "unknown"), "no_live_location_data"
    if baseline_count and live_count < COVERAGE_THRESHOLD * baseline_count:
        return "unknown", f"incomplete_coverage_{live_count}_vs_{baseline_count}"
    if live_fp is not None and live_fp == baseline_fp:
        return "unchanged", "live_fingerprint_matches_baseline"
    return "changed", "live_fingerprint_differs_from_baseline"


def classify_result(prediction, actual):
    """Pure scorer mapping (prediction, actual) -> result label."""
    if actual == "inconclusive":
        return "inconclusive"
    if prediction == "unchanged" and actual == "unchanged":
        return "correct_skip"
    if prediction == "unchanged" and actual == "changed":
        return "missed_change"
    if prediction == "changed" and actual == "changed":
        return "correct_detection"
    if prediction == "changed" and actual == "unchanged":
        return "false_alarm"
    if prediction in ("unknown", "failed"):
        return "fallback_required"
    return "inconclusive"


def load_cohort(path):
    """Return {spider: {'status': actual_status}} for comparable spiders."""
    out = {}
    for r in csv.DictReader(open(path)):
        if r["actual_status"] in ("changed", "unchanged"):
            out[r["spider"]] = r["actual_status"]
    return out


def load_registry(path):
    return list(csv.DictReader(open(path)))


def pick_resources(registry, spider):
    """All monitorable resources (valid http URL) for a spider."""
    res = []
    for r in registry:
        if r["spider"] != spider:
            continue
        url = r["resource_url"]
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        res.append(r)
    return res


def spider_features(data_dir, run_id, spider):
    p = Path(data_dir) / spider / f"{run_id}.geojson"
    return v2.load_geojson(p)


def phase_baseline(cohort, data_b_dir, run_b, outdir):
    """Freeze Data B fingerprints and provenance for the cohort."""
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "baseline_B.jsonl"
    if dest.exists():
        print(f"Baseline already frozen at {dest}; not overwriting.")
        return
    records = []
    for spider in sorted(cohort):
        feats = spider_features(data_b_dir, run_b, spider)
        if not feats:
            continue
        fp = v2.stable_fingerprint(feats)
        src = None
        for f in feats:
            src = (f.get("properties") or {}).get("@source_uri")
            if src:
                break
        records.append({
            "spider": spider,
            "run_b": run_b,
            "extraction_end": "2026-10-25T04:28:58Z",
            "source_file": f"{run_b}/{spider}.geojson",
            "source_uri": src,
            "feature_count": len(feats),
            "stable_fingerprint": fp,
            "baseline_frozen_at": now_iso(),
        })
    with open(dest, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"Baseline frozen: {len(records)} spiders -> {dest}")


def fetch_live(url, method="GET", timeout=20):
    import requests
    t0 = time.time()
    h = {"User-Agent": UA}
    try:
        if method == "POST":
            resp = requests.post(url, timeout=timeout, headers=h, allow_redirects=True)
        else:
            resp = requests.get(url, timeout=timeout, headers=h, allow_redirects=True)
        return {
            "status": resp.status_code,
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "bytes": len(resp.content) if resp.content else 0,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "text": resp.text if resp.ok else None,
            "error": None if resp.ok else f"HTTP {resp.status_code}",
        }
    except Exception as e:
        return {"status": None, "etag": None, "last_modified": None, "bytes": 0,
                "elapsed_ms": int((time.time() - t0) * 1000), "text": None, "error": str(e)}


def phase_monitor(cohort, registry, data_b_dir, run_b, outdir, timeout=20, limit=0):
    """Capture live signals now and freeze predictions."""
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "predictions.jsonl"
    if dest.exists():
        print(f"Predictions already frozen at {dest}; not overwriting.")
        return
    baseline = {}
    for line in (outdir / "baseline_B.jsonl").open():
        r = json.loads(line)
        baseline[r["spider"]] = r

    spiders = sorted(cohort)
    if limit:
        spiders = spiders[:limit]
    preds = []
    for i, spider in enumerate(spiders, 1):
        b = baseline.get(spider)
        if not b:
            continue
        resources = pick_resources(registry, spider)
        if not resources:
            preds.append({
                "spider": spider, "resource_url": "", "decision": "unknown",
                "reason": "no_monitorable_resource", "baseline_count": b["feature_count"],
                "live_count": 0, "requests": 0, "bytes": 0, "elapsed_ms": 0,
                "frozen_at": now_iso(), "run_b": run_b,
            })
            continue

        combined = []
        reqs = 0
        bts = 0
        el = 0
        any_ok = False
        extracted_methods = set()
        for r in resources:
            res = fetch_live(r["resource_url"], r.get("http_method", "GET"), timeout)
            reqs += 1
            bts += res["bytes"]
            el += res["elapsed_ms"]
            if res["text"]:
                feats, fp, meth = ex.extract_json_features(res["text"], spider)
                if feats:
                    combined.extend(feats)
                    any_ok = True
                    extracted_methods.add(meth or "json")
            time.sleep(0.3)

        live_count = len(combined)
        live_fp = v2.stable_fingerprint(combined) if any_ok else None
        decision, reason = classify_prediction(
            any_ok, live_count, b["feature_count"], live_fp, b["stable_fingerprint"])

        preds.append({
            "spider": spider,
            "resource_url": resources[0]["resource_url"] if resources else "",
            "strategy": resources[0].get("primary_strategy", ""),
            "decision": decision,
            "reason": reason,
            "baseline_count": b["feature_count"],
            "live_count": live_count,
            "requests": reqs,
            "bytes": bts,
            "elapsed_ms": el,
            "frozen_at": now_iso(),
            "run_b": run_b,
        })
        if i % 15 == 0:
            print(f"  monitored {i}/{len(spiders)}")

    with open(dest, "w") as f:
        for r in preds:
            f.write(json.dumps(r) + "\n")
    from collections import Counter
    print(f"Predictions frozen: {len(preds)} -> {dest}")
    print(f"  decisions: {dict(Counter(r['decision'] for r in preds))}")


def phase_evaluate(cohort, data_b_dir, run_b, data_a_dir, run_a, outdir):
    """Compare Data A vs Data B and score frozen predictions."""
    preds = [json.loads(line) for line in (outdir / "predictions.jsonl").open()]
    pred_by_spider = {p["spider"]: p for p in preds}

    rows = []
    for spider in sorted(cohort):
        fa = spider_features(data_a_dir, run_a, spider)
        fb = spider_features(data_b_dir, run_b, spider)
        p = pred_by_spider.get(spider)
        if p is None:
            continue
        if not fa or not fb:
            actual = "inconclusive"
            reason = "missing_data"
            added = removed = 0
        else:
            # reuse the validated comparison + truncation guard
            stats = v2.compare_spider(fa, fb)
            lo, hi = min(len(fa), len(fb)), max(len(fa), len(fb))
            if hi == 0 or lo / hi < 0.5:
                actual = "inconclusive"
                reason = f"count_swing_{lo}_{hi}"
            elif stats["changed"]:
                actual = "changed"
                reason = f"added={stats['added']},removed={stats['removed']}"
            else:
                actual = "unchanged"
                reason = "identical"
            added, removed = stats["added"], stats["removed"]

        pred = p["decision"]
        result = classify_result(pred, actual)

        rows.append({
            "spider": spider, "prediction": pred, "actual": actual,
            "result": result, "reason": reason,
            "baseline_count": p["baseline_count"], "live_count": p["live_count"],
            "added": added, "removed": removed,
        })

    # Write CSV + summary
    eval_path = outdir / "evaluation.csv"
    with open(eval_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["spider"])
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    rc = Counter(r["result"] for r in rows)
    ac = Counter(r["actual"] for r in rows)
    summary = {
        "run_b": run_b, "run_a": run_a,
        "evaluated": len(rows),
        "correct_skip": rc.get("correct_skip", 0),
        "missed_change": rc.get("missed_change", 0),
        "correct_detection": rc.get("correct_detection", 0),
        "false_alarm": rc.get("false_alarm", 0),
        "fallback_required": rc.get("fallback_required", 0),
        "inconclusive": rc.get("inconclusive", 0),
        "actual_changed": ac.get("changed", 0),
        "actual_unchanged": ac.get("unchanged", 0),
        "actual_inconclusive": ac.get("inconclusive", 0),
    }
    with open(outdir / "evaluation_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("=== Evaluation ===")
    for k, val in summary.items():
        print(f"  {k}: {val}")
    print(f"Written: {eval_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["baseline", "monitor", "evaluate"])
    ap.add_argument("--run-b", required=True)
    ap.add_argument("--run-a", default=None)
    ap.add_argument("--data-b-dir", default="data/seed/raw")
    ap.add_argument("--data-a-dir", default="data/seed/raw")
    ap.add_argument("--cohort", default="data/seed/two_run_comparison.csv")
    ap.add_argument("--registry", default="data/seed/monitoring_registry.csv")
    ap.add_argument("--outdir", default="data/seed/prospective")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cohort = load_cohort(args.cohort)
    registry = load_registry(args.registry)
    outdir = Path(args.outdir)
    print(f"Cohort: {len(cohort)} comparable spiders; registry: {len(registry)} resources")

    if args.phase == "baseline":
        phase_baseline(cohort, args.data_b_dir, args.run_b, outdir)
    elif args.phase == "monitor":
        phase_monitor(cohort, registry, args.data_b_dir, args.run_b, outdir,
                      timeout=args.timeout, limit=args.limit)
    elif args.phase == "evaluate":
        if not args.run_a:
            print("--run-a required for evaluate")
            sys.exit(1)
        phase_evaluate(cohort, args.data_b_dir, args.run_b, args.data_a_dir, args.run_a, outdir)


if __name__ == "__main__":
    main()
