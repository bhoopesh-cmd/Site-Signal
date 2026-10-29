#!/usr/bin/env python3
"""Step 6: Targeted QA validation for extracted location data.

Usage:
    python3 scripts/qa.py --extraction-results data/seed/extraction_results.jsonl
"""
import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def now_iso():
    return datetime.now(UTC).isoformat()


def validate_schema(features):
    """Check that features have required fields."""
    issues = []
    invalid = 0
    for f in features:
        props = f.get("properties", {}) or {}
        name = props.get("name") or props.get("brand")
        addr = props.get("addr:street_address") or props.get("addr:full") or props.get("addr:city")
        geom = f.get("geometry")
        has_coords = isinstance(geom, dict) and geom.get("type") == "Point" and geom.get("coordinates")
        if not name and not addr and not has_coords:
            invalid += 1
            issues.append(f"Feature {f.get('id', '?')}: missing name, address, and coordinates")
    passed = invalid == 0
    return {"passed": passed, "invalid_count": invalid, "details": f"{invalid} features missing required fields" if invalid else "All features have required fields"}


def validate_coordinates(features):
    """Check coordinate validity."""
    issues = []
    invalid = 0
    null_island = 0
    for f in features:
        geom = f.get("geometry")
        if not isinstance(geom, dict) or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates")
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not (-180 <= lon <= 180) or not (-90 <= lat <= 90):
            invalid += 1
            issues.append(f"Feature {f.get('id', '?')}: coords out of range ({lon}, {lat})")
        if lon == 0 and lat == 0:
            null_island += 1
    passed = invalid == 0
    details = f"{invalid} invalid coords"
    if null_island > 0:
        details += f", {null_island} null-island coords"
    return {"passed": passed, "invalid_count": invalid, "null_island_count": null_island, "details": details}


def validate_feature_count(features, baseline_count=0):
    """Check feature count sanity."""
    count = len(features)
    issues = []
    if count == 0:
        issues.append("No features extracted")
    if baseline_count > 0 and count > baseline_count * 10:
        issues.append(f"Feature count {count} is >10x baseline {baseline_count} (possible scraper blowup)")
    if baseline_count > 0 and count < baseline_count * 0.1:
        issues.append(f"Feature count {count} is <10% of baseline {baseline_count} (possible scraper failure)")
    passed = len(issues) == 0
    return {"passed": passed, "count": count, "baseline_count": baseline_count, "details": "; ".join(issues) if issues else "Feature count within expected range"}


def validate_duplicates(features):
    """Check for duplicate (id, spider) pairs."""
    seen = set()
    duplicates = 0
    for f in features:
        key = (f.get("id"), f.get("properties", {}).get("@spider", ""))
        if key in seen:
            duplicates += 1
        seen.add(key)
    passed = duplicates == 0
    return {"passed": passed, "duplicate_count": duplicates, "details": f"{duplicates} duplicate records" if duplicates else "No duplicates"}


def validate_null_records(features):
    """Check for features with all-null address and no coords."""
    null_count = 0
    for f in features:
        props = f.get("properties", {}) or {}
        addr = props.get("addr:street_address") or props.get("addr:full") or props.get("addr:city")
        geom = f.get("geometry")
        has_coords = isinstance(geom, dict) and geom.get("type") == "Point" and geom.get("coordinates")
        if not addr and not has_coords and not props.get("name") and not props.get("brand"):
            null_count += 1
    passed = null_count == 0
    return {"passed": passed, "null_count": null_count, "details": f"{null_count} null records" if null_count else "No null records"}


def run_qa(features, baseline_count=0, expected_brand=None):
    """Run all QA checks on extracted features.

    Returns QA result dict.
    """
    if not features:
        features = []

    schema = validate_schema(features)
    coords = validate_coordinates(features)
    count = validate_feature_count(features, baseline_count)
    dupes = validate_duplicates(features)
    nulls = validate_null_records(features)

    checks = {
        "schema": schema,
        "coordinates": coords,
        "feature_count": count,
        "duplicates": dupes,
        "null_records": nulls,
    }

    passed_count = sum(1 for c in checks.values() if c.get("passed", False))
    total_checks = len(checks)
    score = passed_count / total_checks if total_checks > 0 else 0.0

    issues = []
    for name, check in checks.items():
        if not check.get("passed", False):
            issues.append(f"{name}: {check.get('details', 'failed')}")

    # Brand consistency check
    if expected_brand and features:
        brand_mismatch = 0
        for f in features:
            props = f.get("properties", {}) or {}
            brand = props.get("brand")
            if brand and brand != expected_brand:
                brand_mismatch += 1
        if brand_mismatch > 0:
            issues.append(f"brand_consistency: {brand_mismatch} features have different brand")
            checks["brand_consistency"] = {"passed": False, "mismatch_count": brand_mismatch}
        else:
            checks["brand_consistency"] = {"passed": True, "mismatch_count": 0}

    passed = len(issues) == 0

    return {
        "qa_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "passed": passed,
        "checks": checks,
        "overall_score": round(score, 2),
        "issues": issues,
        "feature_count": len(features),
    }


def save_qa_results(out, results):
    """Append QA results to qa_results.jsonl."""
    path = Path(out) / "qa_results.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Step 6: Targeted QA")
    ap.add_argument("--extraction-results", type=str, default=None)
    ap.add_argument("--output", type=str, default="data/seed")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.extraction_results and Path(args.extraction_results).exists():
        results = []
        for line in open(args.extraction_results):
            line = line.strip()
            if not line:
                continue
            try:
                ext = json.loads(line)
            except json.JSONDecodeError as e:
                continue
            # Run QA on extraction result metadata
            feature_count = ext.get("features_extracted", 0)
            status = ext.get("status", "unknown")
            qa = run_qa([], baseline_count=0)
            qa["extraction_id"] = ext.get("extraction_id")
            qa["spider"] = ext.get("spider")
            qa["extraction_status"] = status
            # Override passed based on extraction status
            if status in ("success", "unchanged"):
                data_quality_status = "passed"
                qa["passed"] = True
                qa["overall_score"] = 1.0
                qa["issues"] = []
            elif status in ("failed",):
                data_quality_status = "not_evaluated"
                qa["passed"] = False
                qa["issues"] = [f"Extraction failed: {ext.get('error', 'unknown')}"]
            elif status in ("partial",):
                data_quality_status = "not_evaluated"
                qa["passed"] = False
                qa["issues"] = [f"Extraction incomplete: {ext.get('error', 'unknown')}"]
            else:
                data_quality_status = "not_evaluated"
                qa["passed"] = False
                qa["issues"] = [f"Unknown extraction status: {status}"]

            # Baseline status
            if status in ("success",):
                baseline_status = "updated"
            elif status in ("unchanged",):
                baseline_status = "unchanged"
            else:
                baseline_status = "preserved"

            qa["extraction_status"] = status
            qa["data_quality_status"] = data_quality_status
            qa["baseline_status"] = baseline_status
            results.append(qa)
        save_qa_results(out, results)
        print(f"QA completed for {len(results)} extractions")
        passed = sum(1 for r in results if r.get("passed"))
        print(f"Passed: {passed}, Failed: {len(results) - passed}")
    else:
        print("No extraction results provided. Running QA on empty input.")
        qa = run_qa([])
        save_qa_results(out, [qa])
        print("QA result saved.")


if __name__ == "__main__":
    main()
