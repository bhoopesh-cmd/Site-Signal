#!/usr/bin/env python3
"""Step 6: Selective extraction engine for site-signal.

Extracts location data from a resource only when triggered by the monitor.
Usage:
    python3 scripts/extract.py --resource-url URL --spider NAME --strategy A --baseline-id ID
"""
import argparse
import csv as _csv
import hashlib
import json
import sys
import time
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


def extract_json_features(response_text, spider):
    """Extract location features from a JSON response.

    Returns (features_list, fingerprint, extraction_method) or (None, None, None).
    """
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return None, None, None

    features = []
    if isinstance(data, dict):
        for key in ["features", "stores", "data", "locations", "results", "branches",
                    "offices", "items", "records", "places", "points", "markers"]:
            if key in data and isinstance(data[key], list):
                features = data[key]
                break
    elif isinstance(data, list):
        features = data

    if not features:
        return None, None, None

    # Normalize to GeoJSON-like features if needed
    normalized = []
    for f in features:
        if isinstance(f, dict) and "type" in f and f.get("type") == "Feature":
            normalized.append(f)
        elif isinstance(f, dict):
            coords = None
            if "lat" in f and "lon" in f:
                coords = [f["lon"], f["lat"]]
            elif "latitude" in f and "longitude" in f:
                coords = [f["longitude"], f["latitude"]]
            elif "lat" in f and "lng" in f:
                coords = [f["lng"], f["lat"]]
            normalized.append({
                "type": "Feature",
                "id": f.get("id") or f.get("ref") or f.get("code") or str(hash(json.dumps(f, sort_keys=True))),
                "properties": f,
                "geometry": {"type": "Point", "coordinates": coords} if coords else None,
            })

    if not normalized:
        return None, None, None

    fp = bs.compute_fingerprint(normalized)
    return normalized, fp, "json_api"


def extract_sitemap_features(response_text):
    """Extract URLs and lastmod from sitemap XML.

    Returns (urls, lastmods, fingerprint) or (None, None, None).
    """
    import re
    urls = re.findall(r"<loc>\s*(https?://[^<]+)\s*</loc>", response_text, re.I)
    lastmods = re.findall(r"<lastmod>\s*([^<]+)\s*</lastmod>", response_text, re.I)
    if not urls:
        return None, None, None
    fp = bs.compute_sitemap_fingerprint(urls, lastmods if lastmods else None)
    return urls, lastmods, fp


def extract_resource(resource, baseline=None, timeout=15, dry_run=False):
    """Extract location data from a resource.

    Returns extraction result dict.
    """
    spider = resource.get("spider", "")
    url = resource.get("resource_url", "")
    strategy = resource.get("primary_strategy", "E")
    method = resource.get("http_method", "GET") or "GET"

    result = {
        "extraction_id": str(__import__("uuid").uuid4()),
        "spider": spider,
        "resource_url": redact_url(url),
        "timestamp": now_iso(),
        "strategy": strategy,
        "status": "skipped",
        "extraction_method": "none",
        "features_extracted": 0,
        "normalized_fingerprint": None,
        "body_hash": None,
        "baseline_fingerprint": baseline.get("normalized_fingerprint") if baseline else None,
        "data_changed": None,
        "changed_fields": "",
        "error": None,
        "elapsed_ms": 0,
        "bytes_transferred": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        result["status"] = "would_extract"
        result["extraction_method"] = "dry_run"
        return result

    # Strategy D: browser fallback
    if strategy == "D":
        result["status"] = "fallback_needed"
        result["extraction_method"] = "browser_needed"
        result["error"] = "Browser interaction required; fallback to existing scraper"
        return result

    # Strategy E: unresolved
    if strategy == "E":
        result["status"] = "skipped"
        result["extraction_method"] = "unresolved"
        result["error"] = "Resource unresolved; no extraction method available"
        return result

    t0 = time.time()
    try:
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            r = requests.post(url, timeout=timeout, headers=headers, allow_redirects=True)
        else:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)

        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        result["bytes_transferred"] = len(r.content) if r.content else 0

        if not (200 <= r.status_code < 400):
            result["status"] = "failed"
            result["error"] = f"HTTP {r.status_code}"
            return result

        # Compute body hash
        if r.content:
            result["body_hash"] = hashlib.sha256(r.content).hexdigest()

        # Strategy A: Conditional request first
        if strategy == "A":
            etag = resource.get("etag", "") or r.headers.get("ETag", "")
            lm = resource.get("last_modified", "") or r.headers.get("Last-Modified", "")
            if etag:
                cond_headers = {"User-Agent": USER_AGENT, "If-None-Match": etag}
                try:
                    cr = requests.get(url, timeout=timeout, headers=cond_headers, allow_redirects=True)
                    if cr.status_code == 304:
                        # Unchanged — reuse baseline
                        result["status"] = "unchanged"
                        result["extraction_method"] = "conditional_304"
                        result["normalized_fingerprint"] = baseline.get("normalized_fingerprint") if baseline else None
                        result["data_changed"] = False
                        return result
                except Exception:
                    pass

        # Extract features based on content type
        ct = (r.headers.get("Content-Type") or "").lower()
        # Cautious JSON detection when Content-Type is missing or generic
        if not ct or ct in ("text/plain", "application/octet-stream", ""):
            try:
                json.loads(r.text)
                ct = "application/json"
            except (json.JSONDecodeError, TypeError):
                pass
        if "json" in ct:
            features, fp, method_name = extract_json_features(r.text, spider)
            result["extraction_method"] = method_name or "json_api"
        elif "xml" in ct or url.endswith(".xml") or url.endswith(".php"):
            # Check if standard sitemap or custom XML
            if "<urlset" in r.text.lower() or "<sitemapindex" in r.text.lower():
                urls, lastmods, fp = extract_sitemap_features(r.text)
                result["extraction_method"] = "sitemap_crawl"
                result["features_extracted"] = len(urls) if urls else 0
                result["normalized_fingerprint"] = fp
                if baseline and baseline.get("normalized_fingerprint"):
                    result["data_changed"] = (fp != baseline["normalized_fingerprint"])
                result["status"] = "success"
                return result
            else:
                # Custom XML format (not standard sitemap)
                result["extraction_method"] = "custom_xml"
                result["status"] = "partial"
                result["error"] = "Custom XML format; needs specialized parser"
                return result
        elif "csv" in ct or url.endswith(".csv"):
            result["extraction_method"] = "csv"
            result["status"] = "partial"
            result["error"] = "CSV parsing not yet implemented"
            return result
        elif "javascript" in ct:
            result["extraction_method"] = "javascript"
            result["status"] = "partial"
            result["error"] = "JavaScript bundle; needs browser rendering"
            return result
        elif "html" in ct:
            # Try JSON-LD extraction
            features, fp, method_name = extract_json_features(r.text, spider)
            if features:
                result["extraction_method"] = method_name or "html_scrape"
            else:
                result["extraction_method"] = "html_unextractable"
                result["status"] = "partial"
                result["error"] = "No structured data found in HTML; browser may be needed"
                return result
        else:
            result["extraction_method"] = "unknown"
            result["status"] = "partial"
            result["error"] = f"Unsupported content type: {ct}"
            return result

        if features:
            result["features_extracted"] = len(features)
            result["normalized_fingerprint"] = fp
            if baseline and baseline.get("normalized_fingerprint"):
                result["data_changed"] = (fp != baseline["normalized_fingerprint"])
            result["status"] = "success"
        else:
            result["extraction_method"] = "no_features"
            result["status"] = "partial"
            result["error"] = "No location features extracted"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        result["elapsed_ms"] = int((time.time() - t0) * 1000)

    return result


def extract_batch(resources, baselines_map, timeout=15, dry_run=False, concurrency=4):
    """Extract multiple resources with concurrency."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {}
        for r in resources:
            baseline = baselines_map.get((r["spider"], r["resource_url"]))
            futures[ex.submit(extract_resource, r, baseline, timeout, dry_run)] = r
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                r = futures[fut]
                results.append({
                    "extraction_id": str(__import__("uuid").uuid4()),
                    "spider": r["spider"],
                    "resource_url": r["resource_url"],
                    "timestamp": now_iso(),
                    "status": "failed",
                    "error": str(e),
                })
    return results


def save_extraction_results(out, results):
    """Append extraction results to extraction_results.jsonl."""
    path = Path(out) / "extraction_results.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Step 6: Selective extraction")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--registry", type=str, default="data/seed/monitoring_registry.csv")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--max-resources", type=int, default=0)
    ap.add_argument("--spider", type=str, default=None, help="Extract for specific spider")
    ap.add_argument("--strategy", type=str, default=None, help="Extract for specific strategy")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # Load registry
    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"Registry not found: {registry_path}")
        sys.exit(1)
    registry = list(_csv.DictReader(open(registry_path)))

    if args.spider:
        registry = [r for r in registry if r["spider"] == args.spider]
    if args.strategy:
        registry = [r for r in registry if r["primary_strategy"] == args.strategy]
    if args.max_resources > 0:
        registry = [r for r in registry if r.get("primary_strategy") in ("A", "B", "C")]

    # Filter to resources that would be extracted (strategies A, B, C)
    extractable = [r for r in registry if r.get("primary_strategy") in ("A", "B", "C")]

    # Load baselines
    baselines = bs.load_baselines(out)
    baselines_map = {}
    for b in baselines:
        baselines_map[(b["spider"], b["resource_url"])] = b

    dry_run = not args.live
    print(f"Extracting {len(extractable)} resources (dry_run={dry_run})...")

    results = extract_batch(extractable, baselines_map, timeout=args.timeout, dry_run=dry_run, concurrency=args.concurrency)

    save_extraction_results(out, results)

    # Summary
    from collections import Counter
    print("\n=== Extraction Summary ===")
    print(f"  Resources: {len(results)}")
    print(f"  Status: {Counter(r.get('status') for r in results)}")
    print(f"  Features extracted: {sum(r.get('features_extracted', 0) for r in results)}")
    print(f"  Total bytes: {sum(r.get('bytes_transferred', 0) for r in results)}")
    print(f"  Total elapsed: {sum(r.get('elapsed_ms', 0) for r in results)}ms")


if __name__ == "__main__":
    main()
