#!/usr/bin/env python3
"""Validate Site-Signal using two consecutive AllThePlaces runs.

Data B (previous week) is the historical location-data baseline.
Data A (latest week, local) is the fresh location data.
Compare actual location records (name/address/coords, not IDs), then
evaluate Site-Signal's frozen predictions against the actual changes.

Usage:
    python3 scripts/validate_two_runs.py \
        --data-a data/seed/raw \
        --run-a 2026-10-17-13-32-31 \
        --run-b 2026-10-10-13-32-41 \
        --output data/seed \
        --download-b
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_BASE = "https://alltheplaces-data.openaddresses.io/runs"
UA = "ChainXY-seed-collector/1.0 (+research)"


def norm_text(s):
    if s is None:
        return ""
    return " ".join(str(s).split()).strip().lower()


def norm_coord(c):
    """Round coordinates to ~11m precision (4 decimals ~ 11m at equator)."""
    try:
        return round(float(c), 4)
    except (TypeError, ValueError):
        return None


def load_geojson(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("features", []) if d.get("type") == "FeatureCollection" else None
    except Exception:
        return None


def compare_spider(features_a, features_b):
    """Compare two feature lists. Returns change stats.

    Matching strategy: a location is 'same' if its stable primary key matches.
    We fall back to coords-only key for matching when name/address formatting
    differs (harmless change). We do NOT match on record id.
    """
    # Build multi-key maps for B
    b_primary = {}
    b_namecoord = {}
    b_coord = {}
    for f in features_b:
        props = f.get("properties", {}) or {}
        geom = f.get("geometry", {}) or {}
        name = norm_text(props.get("name") or props.get("brand"))
        addr = norm_text(
            props.get("addr:full") or props.get("addr:street_address")
            or props.get("address") or props.get("street")
        )
        coords = geom.get("coordinates")
        ckey = None
        if coords and isinstance(coords, list) and len(coords) >= 2:
            ckey = f"{norm_coord(coords[1])},{norm_coord(coords[0])}"
        primary = "|".join([name, addr, ckey or ""])
        b_primary[primary] = True
        if name and ckey:
            b_namecoord[f"{name}|{ckey}"] = True
        if ckey:
            b_coord[ckey] = True

    added = []
    matched = 0
    for f in features_a:
        props = f.get("properties", {}) or {}
        geom = f.get("geometry", {}) or {}
        name = norm_text(props.get("name") or props.get("brand"))
        addr = norm_text(
            props.get("addr:full") or props.get("addr:street_address")
            or props.get("address") or props.get("street")
        )
        coords = geom.get("coordinates")
        ckey = None
        if coords and isinstance(coords, list) and len(coords) >= 2:
            ckey = f"{norm_coord(coords[1])},{norm_coord(coords[0])}"
        primary = "|".join([name, addr, ckey or ""])

        if primary in b_primary:
            matched += 1
            b_primary[primary] = False
            continue
        # tolerant: same name + coords (address formatting drift)
        if name and ckey and f"{name}|{ckey}" in b_namecoord:
            matched += 1
            b_namecoord[f"{name}|{ckey}"] = False
            continue
        # tolerant: same coords only (name/address drift)
        if ckey and ckey in b_coord:
            matched += 1
            b_coord[ckey] = False
            continue
        added.append(primary)

    removed = [k for k, v in b_primary.items() if v]
    # count removed that were never matched via tolerant keys
    removed_namecoord = [k for k, v in b_namecoord.items() if v]
    removed_coord = [k for k, v in b_coord.items() if v]

    # A location is genuinely removed only if it matches neither primary,
    # name+coord, nor coord in A. Approximate: removed_primary minus tolerant.
    # We conservatively treat coord-only matches as unchanged, so genuine
    # removals are those with no coordinate match in A.
    a_coords = set()
    for f in features_a:
        geom = f.get("geometry", {}) or {}
        coords = geom.get("coordinates")
        if coords and isinstance(coords, list) and len(coords) >= 2:
            a_coords.add(f"{norm_coord(coords[1])},{norm_coord(coords[0])}")
    genuine_removed = [k for k in removed_coord if k not in a_coords]

    return {
        "count_a": len(features_a),
        "count_b": len(features_b),
        "matched": matched,
        "added": len(added),
        "removed": len(genuine_removed),
        "changed": (len(added) > 0 or len(genuine_removed) > 0),
    }


def download_b(spider, run_b, cache_dir):
    """Download Data B for a spider, cached."""
    import urllib.request
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{spider}.geojson"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = f"{DATA_BASE}/{run_b}/output/{spider}.geojson"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


def stable_fingerprint(features):
    """A stable location fingerprint: SHA-256 of sorted, deduplicated
    (name, addr, lat, lon) tuples. Excludes record id to tolerate ID renames.
    """
    if not features:
        return None
    tuples = set()
    for f in features:
        props = f.get("properties", {}) or {}
        geom = f.get("geometry", {}) or {}
        name = norm_text(props.get("name") or props.get("brand"))
        addr = norm_text(
            props.get("addr:full") or props.get("addr:street_address")
            or props.get("address") or props.get("street")
        )
        c = geom.get("coordinates") if isinstance(geom, dict) else None
        coords = ""
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            lat = norm_coord(c[1])
            lon = norm_coord(c[0])
            coords = f"{lat},{lon}"
        tuples.add((name, addr, coords))
    content = json.dumps(sorted(tuples))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_predictions(path):
    """Aggregate Site-Signal predictions per spider from optimized_run.jsonl."""
    preds = {}
    if not Path(path).exists():
        return preds
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            spider = r.get("spider")
            decision = r.get("decision", "unknown")
            if spider not in preds:
                preds[spider] = {"decisions": [], "resources": []}
            preds[spider]["decisions"].append(decision)
            preds[spider]["resources"].append(r.get("resource_url", ""))
    agg = {}
    for spider, d in preds.items():
        decs = d["decisions"]
        if any(x in ("trigger_extraction", "would_trigger_extraction") for x in decs):
            agg[spider] = "changed"
        elif all(x in ("skip", "would_skip") for x in decs):
            agg[spider] = "skip"
        else:
            agg[spider] = "unknown"
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-a", default="data/seed/raw")
    ap.add_argument("--run-a", default="2026-10-17-13-32-31")
    ap.add_argument("--run-b", default="2026-10-10-13-32-41")
    ap.add_argument("--output", default="data/seed")
    ap.add_argument("--download-b", action="store_true")
    ap.add_argument("--cache-b", default="data/seed/raw_b")
    ap.add_argument("--predictions", default="data/seed/optimized_run.jsonl")
    args = ap.parse_args()

    data_a = Path(args.data_a)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    cache_b = Path(args.cache_b)

    spiders = sorted([p.name for p in data_a.iterdir() if p.is_dir()])
    print(f"Data A spiders: {len(spiders)} (run {args.run_a})")
    print(f"Data B run: {args.run_b}")

    # Load predictions (frozen)
    predictions = load_predictions(args.predictions)
    print(f"Frozen Site-Signal predictions for {len(predictions)} spiders")

    rows = []
    for i, spider in enumerate(spiders, 1):
        a_path = data_a / spider / f"{args.run_a}.geojson"
        features_a = load_geojson(a_path) if a_path.exists() else None

        b_path = None
        if args.download_b:
            b_path = download_b(spider, args.run_b, cache_b)
        else:
            candidate = cache_b / f"{spider}.geojson"
            if candidate.exists():
                b_path = candidate

        features_b = load_geojson(b_path) if b_path else None

        pred = predictions.get(spider, "unknown")

        if features_a is None or features_b is None:
            status = "inconclusive"
            reason = "missing_or_unparseable"
            stats = {"count_a": len(features_a) if features_a else 0,
                     "count_b": len(features_b) if features_b else 0,
                     "matched": 0, "added": 0, "removed": 0, "changed": None}
        elif len(features_a) == 0 or len(features_b) == 0:
            status = "inconclusive"
            reason = "empty_dataset"
            stats = {"count_a": len(features_a), "count_b": len(features_b),
                     "matched": 0, "added": 0, "removed": 0, "changed": None}
        else:
            stats = compare_spider(features_a, features_b)
            lo, hi = min(len(features_a), len(features_b)), max(len(features_a), len(features_b))
            # Guard against truncation/duplication: a >50% count swing is not a
            # trustworthy genuine change (per instructions, don't treat incomplete
            # extractions as removals).
            if hi > 0 and lo / hi < 0.5:
                status = "inconclusive"
                reason = f"count_swing_{lo}_vs_{hi}_likely_truncation"
            elif stats["matched"] / max(stats["count_a"], 1) < 0.5 and stats["removed"] > 0:
                status = "inconclusive"
                reason = "low_match_coverage"
            elif stats["changed"]:
                status = "changed"
                reason = f"added={stats['added']},removed={stats['removed']}"
            else:
                status = "unchanged"
                reason = f"matched={stats['matched']}"

        # Fingerprint-based predictions on the A/B pair (Strategy B logic).
        # These are temporally valid: both are real consecutive AllThePlaces runs.
        cur_fp_b = current_fp_a = None
        stable_fp_b = stable_fp_a = None
        pred_current_fp = pred_stable_fp = "n/a"
        if features_a is not None and features_b is not None and features_a and features_b:
            try:
                import baseline_store as bs
                cur_fp_b = bs.compute_fingerprint(features_b)
                cur_fp_a = bs.compute_fingerprint(features_a)
                pred_current_fp = "skip" if (cur_fp_b == cur_fp_a) else "changed"
            except Exception:
                pred_current_fp = "n/a"
            try:
                stable_fp_b = stable_fingerprint(features_b)
                stable_fp_a = stable_fingerprint(features_a)
                pred_stable_fp = "skip" if (stable_fp_b == stable_fp_a) else "changed"
            except Exception:
                pred_stable_fp = "n/a"

        # Evaluate prediction vs actual
        if pred == "skip" and status == "unchanged":
            result = "correct_skip"
        elif pred == "skip" and status == "changed":
            result = "missed_change"
        elif pred == "changed" and status == "changed":
            result = "correct_detection"
        elif pred == "changed" and status == "unchanged":
            result = "unnecessary_extraction"
        elif status == "inconclusive":
            result = "inconclusive"
        elif pred == "unknown":
            result = "fallback_required"
        else:
            result = "inconclusive"

        def eval_fp(p, actual):
            if actual == "inconclusive" or p == "n/a":
                return "inconclusive"
            if p == "skip" and actual == "unchanged":
                return "correct_skip"
            if p == "skip" and actual == "changed":
                return "missed_change"
            if p == "changed" and actual == "changed":
                return "correct_detection"
            if p == "changed" and actual == "unchanged":
                return "unnecessary_extraction"
            return "inconclusive"

        rows.append({
            "spider": spider,
            "prediction": pred,
            "actual_status": status,
            "result": result,
            "reason": reason,
            "count_a": stats["count_a"],
            "count_b": stats["count_b"],
            "matched": stats["matched"],
            "added": stats["added"],
            "removed": stats["removed"],
            "pred_current_fp": pred_current_fp,
            "pred_stable_fp": pred_stable_fp,
            "result_current_fp": eval_fp(pred_current_fp, status),
            "result_stable_fp": eval_fp(pred_stable_fp, status),
        })

        if i % 20 == 0:
            print(f"  processed {i}/{len(spiders)}")

    # Write per-resource CSV
    csv_path = out / "two_run_comparison.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Summary
    from collections import Counter
    result_counts = Counter(r["result"] for r in rows)
    actual_counts = Counter(r["actual_status"] for r in rows)
    pred_counts = Counter(r["prediction"] for r in rows)

    total = len(rows)
    comparable = sum(1 for r in rows if r["actual_status"] in ("changed", "unchanged"))
    correct_skip = result_counts.get("correct_skip", 0)
    missed = result_counts.get("missed_change", 0)
    correct_det = result_counts.get("correct_detection", 0)
    unnec = result_counts.get("unnecessary_extraction", 0)
    fallback = result_counts.get("fallback_required", 0)
    incc = result_counts.get("inconclusive", 0)

    # Accuracy metrics on comparable resources with definitive predictions
    denom_skip = correct_skip + missed
    denom_detect = correct_det + unnec
    denom_all = correct_skip + missed + correct_det + unnec

    # Fingerprint-logic evaluation (temporally valid A/B pair)
    def fp_summary(col_pred, col_result):
        rc = Counter(r[col_result] for r in rows)
        cs = rc.get("correct_skip", 0)
        mc = rc.get("missed_change", 0)
        cd = rc.get("correct_detection", 0)
        ue = rc.get("unnecessary_extraction", 0)
        inc = rc.get("inconclusive", 0)
        d_skip = cs + mc
        d_det = cd + ue
        d_all = cs + mc + cd + ue
        return {
            "correct_skip": cs,
            "missed_change": mc,
            "correct_detection": cd,
            "unnecessary_extraction": ue,
            "inconclusive": inc,
            "skip_precision": round(cs / d_skip * 100, 1) if d_skip else None,
            "detection_precision": round(cd / d_det * 100, 1) if d_det else None,
            "overall_accuracy": round((cs + cd) / d_all * 100, 1) if d_all else None,
        }

    fp_current = fp_summary("pred_current_fp", "result_current_fp")
    fp_stable = fp_summary("pred_stable_fp", "result_stable_fp")

    # ---- Resource-level savings estimate (from registry mapping) ----
    registry_path = out / "monitoring_registry.csv"
    savings = {}
    if registry_path.exists():
        reg = list(csv.DictReader(open(registry_path)))
        from collections import defaultdict
        by_spider = defaultdict(list)
        for r in reg:
            by_spider[r["spider"]].append(r)
        unchanged = [r["spider"] for r in rows if r["actual_status"] == "unchanged"]
        changed = [r["spider"] for r in rows if r["actual_status"] == "changed"]
        unchanged_res = [x for s in unchanged for x in by_spider.get(s, [])]
        changed_res = [x for s in changed for x in by_spider.get(s, [])]
        monitored = len(unchanged_res) + len(changed_res)
        savings = {
            "monitored_resources_covered": monitored,
            "unchanged_resources": len(unchanged_res),
            "changed_resources": len(changed_res),
            "extraction_jobs_avoidable": len(unchanged_res),
            "qa_jobs_avoidable": len(unchanged_res),
            "processing_savings_pct": round(len(unchanged_res) / monitored * 100, 1) if monitored else None,
            "network_savings_pct": 0.0,
            "note": "Strategy B downloads full payload; no bandwidth saving. Processing saving "
                    "assumes a trustworthy cheap signal (conditional HTTP / body hash) precedes extraction.",
        }

    summary = {
        "total_spiders": total,
        "comparable": comparable,
        "actual_changed": actual_counts.get("changed", 0),
        "actual_unchanged": actual_counts.get("unchanged", 0),
        "actual_inconclusive": actual_counts.get("inconclusive", 0),
        "pred_skip": pred_counts.get("skip", 0),
        "pred_changed": pred_counts.get("changed", 0),
        "pred_unknown": pred_counts.get("unknown", 0),
        "correct_skip": correct_skip,
        "missed_change": missed,
        "correct_detection": correct_det,
        "unnecessary_extraction": unnec,
        "fallback_required": fallback,
        "inconclusive": incc,
        "skip_precision": round(correct_skip / denom_skip * 100, 1) if denom_skip else None,
        "detection_precision": round(correct_det / denom_detect * 100, 1) if denom_detect else None,
        "overall_accuracy": round((correct_skip + correct_det) / denom_all * 100, 1) if denom_all else None,
        "fp_current": fp_current,
        "fp_stable": fp_stable,
        "savings": savings,
    }

    print("\n=== Frozen Site-Signal Decision Evaluation ===")
    for k, v in summary.items():
        if k in ("fp_current", "fp_stable", "savings"):
            continue
        print(f"  {k}: {v}")
    print("\n=== Fingerprint Logic on A/B Pair (temporally aligned) ===")
    print("  Current fingerprint (includes IDs):", fp_current)
    print("  Stable fingerprint (excludes IDs):", fp_stable)
    print("\n=== Savings Estimate ===")
    for k, v in savings.items():
        print(f"  {k}: {v}")
    print(f"\nWritten: {csv_path}")

    # Save summary json
    with open(out / "two_run_summary.json", "w") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
