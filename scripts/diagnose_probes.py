#!/usr/bin/env python3
"""Diagnose and resolve failed HTTP probes from the seed dataset."""
import argparse
import csv
import json
import os
import re
import time
from pathlib import Path

import requests

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
GITHUB_RAW = "https://raw.githubusercontent.com/alltheplaces/alltheplaces/master/{}"
LATEST_URL = "https://data.alltheplaces.xyz/runs/latest.json"

def fetch_json(url, timeout=30):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()

def fetch_source(filename, timeout=30):
    url = GITHUB_RAW.format(filename)
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if r.status_code == 200:
            return r.text, "success"
        return None, f"http_{r.status_code}"
    except Exception as e:
        return None, f"error: {e}"

def extract_request_urls(source_text):
    """Extract actual request URLs from spider source using multiple patterns."""
    urls = []
    # start_urls
    for m in re.finditer(r'start_urls\s*[:=].*?\[([^\]]*)\]', source_text, re.S):
        for um in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            urls.append(("start_urls", um.group(1)))
    # sitemap_urls
    for m in re.finditer(r'sitemap_urls\s*[:=].*?\[([^\]]*)\]', source_text, re.S):
        for um in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            urls.append(("sitemap_urls", um.group(1)))
    # Request/JsonRequest/FormRequest with URL
    for m in re.finditer(r'(?:JsonRequest|FormRequest|Request)\(\s*["\']([^"\']+)["\']', source_text):
        urls.append(("request_call", m.group(1)))
    for m in re.finditer(r'(?:JsonRequest|FormRequest|Request)\(\s*f["\']([^"\']+)["\']', source_text):
        urls.append(("fstring_request", m.group(1)))
    # urljoin patterns
    for m in re.finditer(r'urljoin\([^,]+,\s*["\']([^"\']+)["\']', source_text):
        urls.append(("urljoin", m.group(1)))
    # yield Request with variable
    for m in re.finditer(r'yield\s+(?:self\.)?page_request\([^)]*\)', source_text):
        pass
    # f-string URLs assigned to variables
    for m in re.finditer(r'(\w+)\s*=\s*f["\'](https?://[^"\']+)["\']', source_text):
        urls.append(("fstring_var", m.group(2)))
    # any quoted https url (excluding non-source)
    EXCLUDE = ["github.com", "raw.githubusercontent", "pypi.org", "alltheplaces", "openaddresses", "wikidata", "osm.org", "wikipedia", "example.com"]
    for m in re.finditer(r'["\'](https?://[^"\']+)["\']', source_text):
        u = m.group(1)
        if any(d in u.lower() for d in EXCLUDE):
            continue
        urls.append(("quoted_url", u))
    return urls

def classify_failure(spider, url, status, error, source_urls):
    """Classify a failed probe based on URL and source evidence."""
    # Non-URL strings
    if not url or not url.startswith("http"):
        if "class" in url or "xpath" in url.lower() or url.startswith("//*"):
            return "Incorrect or irrelevant URL"
        if url.startswith("?"):
            return "Missing request parameters"
        return "Incorrect or irrelevant URL"
    # Template placeholders
    if "{" in url or "}" in url or "[" in url:
        return "Missing request parameters"
    # Status-based
    if status == 401:
        return "Authentication or access restriction"
    if status == 403:
        return "Authentication or access restriction"
    if status == 404:
        return "Redirect or URL construction issue"
    if status == 410:
        return "Redirect or URL construction issue"
    if status in (500, 502, 503):
        return "Server-side failure"
    if status == 400:
        return "Missing request parameters"
    if error and ("DNS" in error or "resolve" in error.lower() or "NameResolution" in error or "Connection" in error):
        return "Network or DNS failure"
    if error:
        return "Network or DNS failure"
    # Check if URL is a non-data resource
    path = url.split("?")[0].lower()
    if any(k in path for k in ["/maps/", "maps.app.goo.gl", "pillar box", "pillar_box"]):
        return "Incorrect or irrelevant URL"
    return "Unresolved"

def try_request(url, headers=None, timeout=15, method="GET", params=None):
    """Attempt a single HTTP request with given params."""
    try:
        if method == "GET":
            r = requests.get(url, timeout=timeout, headers=headers or {"User-Agent": USER_AGENT}, allow_redirects=True, params=params)
        elif method == "HEAD":
            r = requests.head(url, timeout=timeout, headers=headers or {"User-Agent": USER_AGENT}, allow_redirects=True, params=params)
        return r.status_code, r.url, dict(r.headers), [h.url for h in r.history], None
    except Exception as e:
        return None, url, {}, [], str(e)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="data/seed")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()
    out = Path(args.output)

    # Load failures
    lines = [json.loads(l) for l in open(out / "http_metadata.jsonl")]
    fails = [l for l in lines if not (isinstance(l.get("status"), int) and 200 <= l["status"] < 400)]
    print(f"Total failures: {len(fails)}")

    # Load manifest
    manifest = {r["spider"]: r for r in csv.DictReader(open(out / "manifest.csv"))}

    # Preserve original failures
    original_failures = [dict(f) for f in fails]

    # Process each failure
    diagnostics = []
    for f in fails:
        spider = f["spider"]
        url = f["url"]
        status = f.get("status")
        error = f.get("error")
        m = manifest.get(spider, {})
        filename = m.get("source_file", "")

        # Fetch source
        source_text, src_status = fetch_source(filename, timeout=args.timeout)

        # Extract actual request URLs from source
        source_urls = extract_request_urls(source_text) if source_text else []

        # Classify
        classification = classify_failure(spider, url, status, error, source_urls)

        # Determine best candidate URL from source
        best_url = None
        if source_urls:
            # Prefer request_call or fstring patterns over quoted_url
            for kind, u in source_urls:
                if kind in ("request_call", "fstring_request", "fstring_var", "start_urls"):
                    best_url = u
                    break
            if not best_url:
                # Use first non-template URL
                for kind, u in source_urls:
                    if "{" not in u and "[" not in u and not u.startswith("?") and u.startswith("http"):
                        best_url = u
                        break
            if not best_url and source_urls:
                for kind, u in source_urls:
                    if u.startswith("http"):
                        best_url = u
                        break

        # Retry logic
        retry_status = None
        retry_url = url
        retry_error = None
        retry_headers = {}
        retry_history = []
        retry_classification = classification

        # Only retry if we have a plausible better URL or the original might work with headers
        if best_url and best_url != url and best_url.startswith("http") and "{" not in best_url and "[" not in best_url:
            # Try the better URL
            sc, final, hdrs, hist, err = try_request(best_url, timeout=args.timeout)
            if isinstance(sc, int):
                retry_status = sc
                retry_url = final
                retry_headers = hdrs
                retry_history = hist
                if 200 <= sc < 400:
                    retry_classification = "resolved"
            else:
                retry_error = err
                retry_status = None
        elif "{" in url or "[" in url:
            # Template URL — can't construct without params, mark as missing params
            retry_classification = classification
        else:
            # Try original with browser-like headers
            browser_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
            sc, final, hdrs, hist, err = try_request(url, headers=browser_headers, timeout=args.timeout)
            if isinstance(sc, int):
                retry_status = sc
                retry_url = final
                retry_headers = hdrs
                retry_history = hist
                if 200 <= sc < 400 and sc != status:
                    retry_classification = "resolved"
            else:
                retry_error = err

        # Extract relevant info from retry headers
        retry_etag = retry_headers.get("ETag") if retry_headers else None
        retry_lm = retry_headers.get("Last-Modified") if retry_headers else None
        retry_ct = retry_headers.get("Content-Type") if retry_headers else None

        # Determine if response contains location data
        contains_loc = "unknown"
        if retry_ct:
            ct = retry_ct.lower()
            if "json" in ct:
                contains_loc = "likely"
            elif "html" in ct:
                contains_loc = "maybe (html)"
            elif "xml" in ct:
                contains_loc = "maybe (xml)"

        diag = {
            "spider": spider,
            "brand": m.get("brand", ""),
            "original_url": url,
            "original_status": status,
            "original_error": error or "",
            "final_url_original": f.get("final_url", ""),
            "classification": classification,
            "source_file": filename,
            "source_fetch_status": src_status,
            "source_urls_found": ";".join(u for _, u in source_urls[:5]),
            "best_candidate_url": best_url or "",
            "retry_url": retry_url,
            "retry_status": retry_status if retry_status is not None else "",
            "retry_error": retry_error or "",
            "retry_etag": retry_etag or "",
            "retry_last_modified": retry_lm or "",
            "retry_content_type": retry_ct or "",
            "contains_location_data": contains_loc,
            "resolved": "yes" if retry_classification == "resolved" else "no",
            "final_classification": retry_classification,
        }
        diagnostics.append(diag)

        # Rate limit between requests
        time.sleep(0.5)

    # Write diagnostics CSV
    diag_path = out / "probe_diagnostics.csv"
    with open(diag_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(diagnostics[0].keys()))
        w.writeheader()
        for d in diagnostics:
            w.writerow(d)

    # Summary
    from collections import Counter
    print("\n=== Classification Summary ===")
    print(Counter(d["classification"] for d in diagnostics))
    print("\n=== Resolution Summary ===")
    print(Counter(d["resolved"] for d in diagnostics))
    print(f"\nTotal failures: {len(diagnostics)}")
    print(f"Resolved: {sum(1 for d in diagnostics if d['resolved']=='yes')}")
    print(f"Unresolved: {sum(1 for d in diagnostics if d['resolved']=='no')}")
    print(f"\nDiagnostics written to {diag_path}")

if __name__ == "__main__":
    main()
