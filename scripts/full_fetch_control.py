#!/usr/bin/env python3
"""Full-scrape control: unconditionally fetch, extract, normalize, QA every resource.

This is the "conventional scraping" baseline for comparison with Site-Signal.
For every registry resource, fetch unconditionally (no validator/cache skips),
retrieve the payload, extract location data, normalize, run QA.

Usage:
    python3 scripts/full_fetch_control.py --output data/seed --timeout 15
"""
import argparse
import hashlib
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as ex
import qa

import collect_seed as cs

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
TOKEN_RE = cs.TOKEN_RE


def now_iso():
    return datetime.now(UTC).isoformat()


def redact_url(url):
    return cs.redact_url(url)


def fetch_control(resource, timeout=15):
    """Unconditionally fetch, extract, normalize, QA one resource."""
    spider = resource.get("spider", "")
    url = resource.get("resource_url", "")
    method = resource.get("http_method", "GET") or "GET"
    strategy = resource.get("primary_strategy", "E")

    result = {
        "run_id": None,  # filled by caller
        "run_timestamp": None,
        "spider": spider,
        "resource_url": redact_url(url),
        "strategy": strategy,
        "http_method": method,
        "status_code": None,
        "requests_issued": 0,
        "bytes_transferred": 0,
        "elapsed_ms": 0,
        "extraction_method": "none",
        "features_extracted": 0,
        "completeness": "failed",
        "qa_passed": False,
        "qa_score": 0.0,
        "fingerprint": None,
        "body_hash": None,
        "error": None,
        "timestamp": now_iso(),
    }

    t0 = time.time()
    try:
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            r = requests.post(url, timeout=timeout, headers=headers, allow_redirects=True)
        else:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)

        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["status_code"] = r.status_code
        result["requests_issued"] = 1
        result["bytes_transferred"] = len(r.content) if r.content else 0

        if not (200 <= r.status_code < 400):
            result["error"] = f"HTTP {r.status_code}"
            result["completeness"] = "failed"
            return result

        # Compute body hash
        if r.content:
            result["body_hash"] = hashlib.sha256(r.content).hexdigest()

        # Extract features
        ct = (r.headers.get("Content-Type") or "").lower()
        if not ct:
            try:
                import json as _json
                _json.loads(r.text)
                ct = "application/json"
            except Exception:
                pass

        features = None
        fp = None
        method_name = None

        if "json" in ct:
            features, fp, method_name = ex.extract_json_features(r.text, spider)
            result["extraction_method"] = method_name or "json_api"
        elif "xml" in ct or url.endswith(".xml") or url.endswith(".php"):
            if "<urlset" in r.text.lower() or "<sitemapindex" in r.text.lower():
                urls, lastmods, fp = ex.extract_sitemap_features(r.text)
                result["extraction_method"] = "sitemap_crawl"
                result["features_extracted"] = len(urls) if urls else 0
                result["completeness"] = "complete" if urls else "partial"
                result["fingerprint"] = fp
                result["status"] = "success" if urls else "partial"
                # QA
                qa_result = qa.run_qa([], baseline_count=result["features_extracted"])
                result["qa_passed"] = qa_result["passed"]
                result["qa_score"] = qa_result["overall_score"]
                return result
            else:
                result["extraction_method"] = "custom_xml"
                result["completeness"] = "partial"
                result["error"] = "Custom XML format"
                return result
        elif "html" in ct or url.endswith(".html") or url.endswith("/"):
            features, fp, method_name = ex.extract_json_features(r.text, spider)
            if features:
                result["extraction_method"] = method_name or "html_scrape"
            else:
                result["extraction_method"] = "html_unextractable"
                result["completeness"] = "partial"
                result["error"] = "No structured data in HTML"
                return result
        else:
            result["extraction_method"] = "unknown"
            result["completeness"] = "partial"
            result["error"] = f"Unsupported content type: {ct}"
            return result

        if features:
            result["features_extracted"] = len(features)
            result["normalized_fingerprint"] = fp
            result["fingerprint"] = fp
            result["completeness"] = "complete"
            # QA
            qa_result = qa.run_qa(features, baseline_count=len(features))
            result["qa_passed"] = qa_result["passed"]
            result["qa_score"] = qa_result["overall_score"]
        else:
            result["extraction_method"] = "no_features"
            result["completeness"] = "partial"
            result["error"] = "No location features extracted"
            result["qa_passed"] = False
            result["qa_score"] = 0.0

    except Exception as e:
        result["error"] = str(e)
        result["completeness"] = "failed"
        result["elapsed_ms"] = int((time.time() - t0) * 1000)

    return result


def fetch_control_batch(resources, timeout=15, concurrency=4):
    """Fetch control for multiple resources with concurrency."""

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(fetch_control, r, timeout): r for r in resources}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                r = futures[fut]
                results.append({
                    "spider": r["spider"],
                    "resource_url": redact_url(r["resource_url"]),
                    "strategy": r.get("primary_strategy", "E"),
                    "status_code": None,
                    "requests_issued": 0,
                    "bytes_transferred": 0,
                    "elapsed_ms": 0,
                    "extraction_method": "none",
                    "features_extracted": 0,
                    "completeness": "failed",
                    "qa_passed": False,
                    "qa_score": 0.0,
                    "fingerprint": None,
                    "body_hash": None,
                    "error": str(e),
                    "timestamp": now_iso(),
                })
    return results


def main():
    ap = argparse.ArgumentParser(description="Full-scrape control for benchmarking")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-resources", type=int, default=0)
    ap.add_argument("--label", type=str, default="control")
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

    run_id = str(uuid.uuid4())[:8]
    run_ts = now_iso()
    print(f"Control run {run_id} — {len(registry)} resources (concurrency={args.concurrency})")

    results = fetch_control_batch(registry, timeout=args.timeout, concurrency=args.concurrency)

    # Add run metadata
    for r in results:
        r["run_id"] = run_id
        r["run_timestamp"] = run_ts
        r["label"] = args.label

    # Append to control_run.jsonl
    control_path = out / "control_run.jsonl"
    with open(control_path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    # Summary
    from collections import Counter
    print("\n=== Control Run Summary ===")
    print(f"  Resources: {len(results)}")
    print(f"  Complete: {sum(1 for r in results if r['completeness'] == 'complete')}")
    print(f"  Partial: {sum(1 for r in results if r['completeness'] == 'partial')}")
    print(f"  Failed: {sum(1 for r in results if r['completeness'] == 'failed')}")
    print(f"  Total requests: {sum(r['requests_issued'] for r in results)}")
    print(f"  Total bytes: {sum(r['bytes_transferred'] for r in results)}")
    print(f"  Total elapsed: {sum(r['elapsed_ms'] for r in results)}ms")
    print(f"  QA passed: {sum(1 for r in results if r['qa_passed'])}")
    print(f"  Total features: {sum(r['features_extracted'] for r in results)}")
    print(f"  Extraction methods: {dict(Counter(r['extraction_method'] for r in results))}")
    print(f"\nAppended to: {control_path}")


if __name__ == "__main__":
    main()
