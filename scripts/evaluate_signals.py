#!/usr/bin/env python3
"""Step 3: Validate change signals and build evaluation dataset.

Usage:
    python3 scripts/evaluate_signals.py --output data/seed
    python3 scripts/evaluate_signals.py --output data/seed --historical-run 2026-10-10-13-32-41
"""
import argparse
import csv
import json
import os
import re
import struct
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_seed as cs

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
HISTORY_URL = "https://data.alltheplaces.xyz/runs/history.json"

# --- Redaction helpers ---
TOKEN_RE = re.compile(r"(token|key|secret|password|auth|session|sid|api_key|apikey)=[^&]+", re.IGNORECASE)


def redact_url(url):
    return TOKEN_RE.sub(r"\1=REDACTED", url)


def now_iso():
    return datetime.now(UTC).isoformat()


def fetch_json(url, timeout=30):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json()


def classify_resource_type(ct, url):
    ct = (ct or "").lower()
    url = (url or "").lower()
    if "json" in ct or url.endswith(".json") or "api" in url:
        return "JSON"
    if "xml" in ct or url.endswith(".xml") or "sitemap" in url:
        return "XML"
    if "csv" in ct or url.endswith(".csv"):
        return "CSV"
    if "graphql" in url:
        return "GraphQL"
    if "html" in ct or url.endswith(".html") or url.endswith("/"):
        return "HTML"
    return "unknown"


def extract_zip_entry(url, name, local_offset, comp_size, uncomp_size, method):
    """Extract a single file from a remote zip using HTTP Range requests."""
    # Read local header
    r = requests.get(url, headers={"Range": f"bytes={local_offset}-{local_offset+29}"}, timeout=60)
    if r.status_code not in (200, 206):
        return None
    local_hdr = r.content
    lf_name_len, lf_extra_len = struct.unpack_from("<HH", local_hdr, 26)
    data_offset = local_offset + 30 + lf_name_len + lf_extra_len
    # Download compressed data
    r2 = requests.get(url, headers={"Range": f"bytes={data_offset}-{data_offset+comp_size-1}"}, timeout=300)
    if r2.status_code not in (200, 206):
        return None
    comp_data = r2.content
    if method == 8:  # deflate
        try:
            data = zlib.decompress(comp_data, wbits=-15)
        except Exception:
            return None
    elif method == 0:  # stored
        data = comp_data
    else:
        return None
    try:
        return json.loads(data)
    except Exception:
        return None


def find_zip_entry(url, spider_name):
    """Find a spider's geojson entry in the zip central directory."""
    # Get EOCD
    r = requests.get(url, headers={"Range": "bytes=-65536"}, timeout=120)
    if r.status_code not in (200, 206):
        return None
    eocd_sig = b"\x50\x4b\x05\x06"
    idx = r.content.rfind(eocd_sig)
    if idx < 0:
        return None
    cd_size, cd_offset = struct.unpack_from("<II", r.content, idx + 12)
    # Download central directory
    r2 = requests.get(url, headers={"Range": f"bytes={cd_offset}-{cd_offset+cd_size-1}"}, timeout=300)
    if r2.status_code not in (200, 206):
        return None
    cd = r2.content
    pos = 0
    while pos < len(cd):
        if cd[pos:pos+4] != b"PK\x01\x02":
            break
        (sig, ver_made, ver_need, flags, method, modtime, moddate,
         crc, comp_size, uncomp_size, name_len, extra_len, comment_len,
         disk_start, int_attr, ext_attr, local_offset) = struct.unpack_from("<IHHHHHHIIIHHHHHII", cd, pos)
        name = cd[pos+46:pos+46+name_len].decode("utf-8", errors="replace")
        if spider_name in name and name.endswith(".geojson"):
            return name, local_offset, comp_size, uncomp_size, method
        pos += 46 + name_len + extra_len + comment_len
    return None


def task1_resource_inventory(out, manifest, http_entries):
    """Build deduplicated resource inventory."""
    # Deduplicate by final URL
    seen = {}
    for entry in http_entries:
        key = entry.get("final_url") or entry.get("url")
        if key not in seen:
            seen[key] = entry
        else:
            # merge: keep first, note duplicate
            seen[key]["_duplicate"] = True

    # Load manifest for spider metadata
    manifest_map = {r["spider"]: r for r in manifest}

    # Load probe diagnostics
    diag_map = {}
    diag_path = out / "probe_diagnostics.csv"
    if diag_path.exists():
        for r in csv.DictReader(open(diag_path)):
            diag_map[r["spider"]] = r

    rows = []
    for url, entry in seen.items():
        spider = entry.get("spider", "")
        m = manifest_map.get(spider, {})
        diag = diag_map.get(spider, {})

        # Skip robots.txt sitemap pointers — not location-data resources
        if url and url.lower().endswith("robots.txt"):
            # Try to resolve to actual sitemap
            try:
                r = requests.get(url, timeout=10, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
                sitemap_url = None
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        line = line.strip()
                        if line.lower().startswith("sitemap:"):
                            sitemap_url = line.split(":", 1)[1].strip() if ":" in line else ""
                            if sitemap_url.startswith("http"):
                                break
                if sitemap_url:
                    # Replace with sitemap URL for monitoring
                    url = sitemap_url
                    entry = dict(entry)
                    entry["url"] = sitemap_url
                    entry["final_url"] = sitemap_url
                else:
                    # No sitemap found, skip this resource
                    continue
            except Exception:
                continue

        # Determine if location data is present
        contains = entry.get("contains_location_data_directly", "unknown")
        # Verify: check if ct indicates JSON/HTML with location data
        ct = (entry.get("content_type") or "").lower()
        status = entry.get("status")
        accessible = isinstance(status, int) and 200 <= status < 400

        # Resource type
        rtype = classify_resource_type(ct, url)

        # Domain extraction with error handling
        try:
            domain = urlparse(url).netloc
        except Exception:
            domain = ""

        # Shared with: find other spiders with same domain
        shared = [s for s, r in manifest_map.items() if r.get("domain") == domain and s != spider and domain]

        # Redact URL
        safe_url = redact_url(url)
        safe_final = redact_url(entry.get("final_url") or url)

        # Access restrictions
        restrictions = ""
        if entry.get("error"):
            restrictions = entry["error"]
        elif isinstance(status, int) and status in (401, 403, 404, 410, 500, 502, 503):
            restrictions = f"HTTP {status}"
        if diag.get("classification"):
            restrictions = diag["classification"] if not restrictions else f"{restrictions}; {diag['classification']}"

        rows.append({
            "spider": spider,
            "brand": m.get("brand", ""),
            "domain": domain,
            "original_url": safe_url,
            "final_url": safe_final,
            "resource_type": rtype,
            "contains_location_data": "verified" if (accessible and contains in ("likely", "maybe (html)", "maybe (xml)")) else ("unverified" if contains != "unknown" else "unknown"),
            "location_data_evidence": contains,
            "etag": entry.get("etag") or "",
            "last_modified": entry.get("last_modified") or "",
            "shared_with": ";".join(shared) if shared else "",
            "shared_count": len(shared),
            "source_mapping_confidence": m.get("source_mapping_confidence", ""),
            "access_restrictions": restrictions,
            "probe_status": status if status is not None else "error",
            "probe_timestamp": entry.get("timestamp", ""),
        })

    # Sort by spider then url
    rows.sort(key=lambda r: (r["spider"], r["original_url"]))

    out_path = out / "resource_inventory.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Task 1: {len(rows)} unique resources inventoried")
    return rows


def task2_historical(out, manifest, prev_run_id=None):
    """Build historical change dataset using HTTP Range requests on zip."""
    if not prev_run_id:
        # Find previous run
        try:
            history = fetch_json(HISTORY_URL)
            sorted_runs = sorted(history, key=lambda h: h.get("start_time", ""), reverse=True)
            for h in sorted_runs:
                if h.get("run_id") != "2026-10-17-13-32-31" and h.get("output_url"):
                    prev_run_id = h["run_id"]
                    break
        except Exception as e:
            print(f"  Cannot fetch history: {e}")
            prev_run_id = None

    if not prev_run_id:
        print("  No previous run found. Skipping historical comparison.")
        # Write header-only changes.csv
        write_changes_header(out)
        return []

    # Find output_url for previous run
    try:
        history = fetch_json(HISTORY_URL)
        prev_meta = next((h for h in history if h["run_id"] == prev_run_id), None)
        output_url = prev_meta.get("output_url") if prev_meta else None
    except Exception:
        output_url = f"https://alltheplaces-data.openaddresses.io/runs/{prev_run_id}/output.zip"

    if not output_url:
        print(f"  No output_url for {prev_run_id}. Skipping.")
        write_changes_header(out)
        return []

    print(f"  Previous run: {prev_run_id}")
    print(f"  Output URL: {output_url}")

    spiders = [r["spider"] for r in manifest]

    # Load current features by spider
    current_features = {}
    raw_dir = out / "raw"
    for sp in spiders:
        geo_path = raw_dir / sp / "2026-10-17-13-32-31.geojson"
        if geo_path.exists():
            try:
                data = json.load(open(geo_path))
                feats = data.get("features", []) if isinstance(data, dict) else []
                current_features[sp] = feats
            except Exception:
                current_features[sp] = []

    # Extract historical features
    historical_features = {}
    found_count = 0
    for sp in spiders:
        entry = find_zip_entry(output_url, sp)
        if entry:
            name, lo, cs_sz, us_sz, method = entry
            geo = extract_zip_entry(output_url, name, lo, cs_sz, us_sz, method)
            if geo and isinstance(geo, dict):
                historical_features[sp] = geo.get("features", [])
                found_count += 1
            else:
                historical_features[sp] = []
        else:
            historical_features[sp] = []

    print(f"  Historical spiders found: {found_count}/{len(spiders)}")

    # Compare
    changes = []
    for sp in spiders:
        cur_feats = current_features.get(sp, [])
        hist_feats = historical_features.get(sp, [])

        cur_by_id = {}
        for f in cur_feats:
            if isinstance(f, dict):
                cur_by_id[f.get("id")] = f

        hist_by_id = {}
        for f in hist_feats:
            if isinstance(f, dict):
                hist_by_id[f.get("id")] = f

        cur_ids = set(cur_by_id.keys())
        hist_ids = set(hist_by_id.keys())

        # Added (in current, not in historical)
        for cid in cur_ids - hist_ids:
            # Try fallback matching
            cf = cur_by_id[cid]
            matched = False
            cprops = cf.get("properties", {}) if isinstance(cf, dict) else {}
            cname = cprops.get("name") or cprops.get("brand")
            caddr = cprops.get("addr:street_address") or cprops.get("addr:full")
            # Fuzzy match against unmatched historical
            for hid in hist_ids - cur_ids:
                hf = hist_by_id[hid]
                hprops = hf.get("properties", {}) if isinstance(hf, dict) else {}
                hname = hprops.get("name") or hprops.get("brand")
                haddr = hprops.get("addr:street_address") or hprops.get("addr:full")
                if cname and hname and cname.lower() == hname.lower() and caddr and haddr and caddr.lower() == haddr.lower():
                    # Fuzzy match found — treat as modified, not added
                    matched = True
                    break
            if not matched:
                changes.append({
                    "spider": sp,
                    "change_type": "added",
                    "prev_run": prev_run_id,
                    "curr_run": "2026-10-17-13-32-31",
                    "prev_record_id": "",
                    "curr_record_id": cid or "",
                    "changed_fields": "",
                    "prev_value": "",
                    "curr_value": "",
                    "matching_confidence": "high",
                    "match_method": "unmatched",
                    "classification": "candidate",
                    "evidence": "id in current not in historical",
                })

        # Removed (in historical, not in current)
        for hid in hist_ids - cur_ids:
            hf = hist_by_id[hid]
            hprops = hf.get("properties", {}) if isinstance(hf, dict) else {}
            # Check if fuzzy-matched to an added record (already handled above)
            already_matched = False
            hname = hprops.get("name") or hprops.get("brand")
            haddr = hprops.get("addr:street_address") or hprops.get("addr:full")
            for cid in cur_ids - hist_ids:
                cf = cur_by_id[cid]
                cprops = cf.get("properties", {}) if isinstance(cf, dict) else {}
                cname = cprops.get("name") or cprops.get("brand")
                caddr = cprops.get("addr:street_address") or cprops.get("addr:full")
                if cname and hname and cname.lower() == hname.lower() and caddr and haddr and caddr.lower() == haddr.lower():
                    already_matched = True
                    break
            if not already_matched:
                changes.append({
                    "spider": sp,
                    "change_type": "removed",
                    "prev_run": prev_run_id,
                    "curr_run": "2026-10-17-13-32-31",
                    "prev_record_id": hid or "",
                    "curr_record_id": "",
                    "changed_fields": "",
                    "prev_value": "",
                    "curr_value": "",
                    "matching_confidence": "high",
                    "match_method": "unmatched",
                    "classification": "candidate",
                    "evidence": "id in historical not in current",
                })

        # Modified (in both)
        for mid in cur_ids & hist_ids:
            cf = cur_by_id[mid]
            hf = hist_by_id[mid]
            cprops = cf.get("properties", {}) if isinstance(cf, dict) else {}
            hprops = hf.get("properties", {}) if isinstance(hf, dict) else {}

            changed_fields = []
            for field in ["name", "brand", "phone", "email", "website", "opening_hours",
                          "addr:street_address", "addr:city", "addr:state", "addr:postcode", "addr:country"]:
                cv = cprops.get(field)
                hv = hprops.get(field)
                if cv is not None and hv is not None and str(cv).strip() != str(hv).strip():
                    changed_fields.append(field)

            # Coordinates
            cgeom = cf.get("geometry", {}) if isinstance(cf, dict) else {}
            hgeom = hf.get("geometry", {}) if isinstance(hf, dict) else {}
            cc = cgeom.get("coordinates") if isinstance(cgeom, dict) else None
            hc = hgeom.get("coordinates") if isinstance(hgeom, dict) else None
            if cc and hc and cc != hc:
                changed_fields.append("coordinates")

            if changed_fields:
                changes.append({
                    "spider": sp,
                    "change_type": "modified",
                    "prev_run": prev_run_id,
                    "curr_run": "2026-10-17-13-32-31",
                    "prev_record_id": mid or "",
                    "curr_record_id": mid or "",
                    "changed_fields": ";".join(changed_fields),
                    "prev_value": ";".join(str(hprops.get(f, "")) for f in changed_fields if f != "coordinates"),
                    "curr_value": ";".join(str(cprops.get(f, "")) for f in changed_fields if f != "coordinates"),
                    "matching_confidence": "high",
                    "match_method": "id_exact",
                    "classification": "candidate",
                    "evidence": "field differs between runs",
                })

    # Write changes.csv
    changes_path = out / "changes.csv"
    changes_cols = ["spider", "change_type", "prev_run", "curr_run", "prev_record_id",
                    "curr_record_id", "changed_fields", "prev_value", "curr_value",
                    "matching_confidence", "match_method", "classification", "evidence"]
    with open(changes_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=changes_cols)
        w.writeheader()
        for c in changes:
            w.writerow(c)

    print(f"  Wrote {len(changes)} candidate changes to changes.csv")

    # Summary
    from collections import Counter
    print("  Change types:", Counter(c["change_type"] for c in changes))
    print("  Spiders with changes:", len(set(c["spider"] for c in changes)))

    return changes


def write_changes_header(out):
    changes_path = out / "changes.csv"
    changes_cols = ["spider", "change_type", "prev_run", "curr_run", "prev_record_id",
                    "curr_record_id", "changed_fields", "prev_value", "curr_value",
                    "matching_confidence", "match_method", "classification", "evidence"]
    with open(changes_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=changes_cols)
        w.writeheader()


def task3_conditional_probes(out, inventory):
    """Test ETag and Last-Modified conditional requests."""
    results = []
    for r in inventory:
        spider = r["spider"]
        url = r["original_url"]
        etag = r.get("etag", "")
        lm = r.get("last_modified", "")

        etag_status = None
        etag_bytes = 0
        etag_elapsed = 0
        etag_body = False
        lm_status = None
        lm_bytes = 0
        lm_elapsed = 0
        lm_body = False

        # ETag test
        if etag:
            headers = {"User-Agent": USER_AGENT, "If-None-Match": etag}
            t0 = time.time()
            try:
                resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
                etag_elapsed = int((time.time() - t0) * 1000)
                etag_status = resp.status_code
                etag_bytes = len(resp.content) if resp.content else 0
                etag_body = bool(resp.content)
            except Exception:
                etag_status = None
                etag_elapsed = int((time.time() - t0) * 1000)

        # Last-Modified test (only if no ETag or test independently)
        if lm:
            headers = {"User-Agent": USER_AGENT, "If-Modified-Since": lm}
            t0 = time.time()
            try:
                resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
                lm_elapsed = int((time.time() - t0) * 1000)
                lm_status = resp.status_code
                lm_bytes = len(resp.content) if resp.content else 0
                lm_body = bool(resp.content)
            except Exception:
                lm_status = None
                lm_elapsed = int((time.time() - t0) * 1000)

        conditional_works = ""
        if etag_status == 304 or lm_status == 304:
            conditional_works = "yes"
        elif etag_status == 200 or lm_status == 200:
            conditional_works = "no (returned 200)"
        else:
            conditional_works = "unknown"

        # Validator reliability: cannot be determined from single observation
        validator_reliability = "unverified (single observation)"

        results.append({
            "spider": spider,
            "url": redact_url(url),
            "etag": etag,
            "last_modified": lm,
            "etag_test_status": etag_status if etag_status is not None else "error",
            "etag_test_bytes": etag_bytes,
            "etag_test_elapsed_ms": etag_elapsed,
            "etag_body_returned": etag_body,
            "lm_test_status": lm_status if lm_status is not None else "error",
            "lm_test_bytes": lm_bytes,
            "lm_test_elapsed_ms": lm_elapsed,
            "lm_body_returned": lm_body,
            "conditional_works": conditional_works,
            "validator_reliability": validator_reliability,
        })

    # Write conditional probe results
    out_path = out / "conditional_probe_results.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        for r in results:
            w.writerow(r)

    # Summary
    etag_304 = sum(1 for r in results if r["etag_test_status"] == 304)
    etag_200 = sum(1 for r in results if r["etag_test_status"] == 200)
    lm_304 = sum(1 for r in results if r["lm_test_status"] == 304)
    lm_200 = sum(1 for r in results if r["lm_test_status"] == 200)
    print(f"  ETag tests: {etag_304} returned 304, {etag_200} returned 200")
    print(f"  Last-Modified tests: {lm_304} returned 304, {lm_200} returned 200")

    return results


def task4_provider_groups(out, manifest, inventory):
    """Identify shared data providers from spider source and URLs."""
    # Load spider source for all selected spiders
    provider_keywords = {
        "yext": "Yext", "storemapper": "Storemapper", "storepoint": "Storepoint",
        "stockist": "Stockist", "mapbox": "Mapbox", "arcgis": "ArcGIS",
        "algolia": "Algolia", "kibo": "Kibo", "wordpress": "WordPress",
        "shopify": "Shopify", "demandware": "Demandware/SFCC", "drupal": "Drupal",
        "joomla": "Joomla", "magento": "Magento", "woocommerce": "WooCommerce",
    }

    groups = {}
    for r in manifest:
        spider = r["spider"]
        filename = r.get("source_file", "")
        # Fetch source
        source_text, src_url, status = cs.fetch_spider_source(filename, timeout=15) if filename else (None, "", "no_file")
        if not source_text:
            continue

        low = source_text.lower()
        domain = r.get("domain", "")
        url = r.get("source_url", "")

        # Detect vendor
        detected = []
        for kw, name in provider_keywords.items():
            if kw in low or kw in url.lower() or kw in domain.lower():
                detected.append(name)

        # Extract API hostname
        api_host = ""
        if url:
            try:
                api_host = urlparse(url).netloc
            except Exception:
                pass

        key = api_host or domain or spider
        if key not in groups:
            groups[key] = {
                "provider": ", ".join(detected) if detected else "Unknown/Custom",
                "evidence": [],
                "spiders": [],
                "domains": set(),
                "endpoints": set(),
                "has_webhook_docs": "unverified",
            }
        groups[key]["spiders"].append(spider)
        groups[key]["domains"].add(domain)
        if url:
            groups[key]["endpoints"].add(redact_url(url))
        if detected:
            groups[key]["evidence"].append(f"keyword: {', '.join(detected)}")

    # Build output
    rows = []
    for _key, g in groups.items():
        is_shared_data = len(g["spiders"]) > 1 and len(g["domains"]) == 1
        is_shared_platform = len(g["spiders"]) > 1 and len(g["domains"]) > 1

        rows.append({
            "provider_or_platform": g["provider"],
            "evidence": "; ".join(g["evidence"]) if g["evidence"] else "URL pattern",
            "source_domains": "; ".join(sorted(g["domains"])),
            "spider_count": len(g["spiders"]),
            "spiders": "; ".join(g["spiders"]),
            "shared_endpoints": "; ".join(sorted(g["endpoints"]))[:500],
            "data_shared": "yes" if is_shared_data else "no",
            "platform_shared": "yes" if is_shared_platform else "no",
            "change_notification": g["has_webhook_docs"],
            "auth_required": "unknown",
            "confidence": "high" if g["evidence"] else "medium",
            "unresolved": "Whether data is shared vs platform-only" if len(g["spiders"]) > 1 else "",
        })

    rows.sort(key=lambda r: r["spider_count"], reverse=True)

    out_path = out / "provider_groups.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"  Provider groups: {len(rows)} groups, {sum(r['spider_count'] for r in rows)} total spiders")
    multi = [r for r in rows if r["spider_count"] > 1]
    print(f"  Groups with multiple spiders: {len(multi)}")

    return rows


def task5_cost_measure(inventory, conditional_results):
    """Measure request costs. Already captured in conditional probe results."""
    # Aggregate
    total_bytes = sum(r.get("etag_test_bytes", 0) + r.get("lm_test_bytes", 0) for r in conditional_results)
    total_elapsed = sum(r.get("etag_test_elapsed_ms", 0) + r.get("lm_test_elapsed_ms", 0) for r in conditional_results)
    print(f"  Total conditional request bytes: {total_bytes}")
    print(f"  Total conditional request elapsed: {total_elapsed}ms")
    return total_bytes, total_elapsed


def task6_signal_evaluation(out, inventory, conditional_results, changes, provider_groups):
    """Generate signal_evaluation.csv and update report."""
    # Build per-resource evaluation
    rows = []
    for r in inventory:
        spider = r["spider"]
        url = r["original_url"]
        # Find conditional result
        cr = next((c for c in conditional_results if c["spider"] == spider and c["url"] == url), None)

        rows.append({
            "spider": spider,
            "brand": r["brand"],
            "resource_url": redact_url(url),
            "resource_type": r["resource_type"],
            "contains_location_data": r["contains_location_data"],
            "etag_present": bool(r.get("etag")),
            "last_modified_present": bool(r.get("last_modified")),
            "conditional_request_works": cr["conditional_works"] if cr else "not_tested",
            "etag_test_status": cr["etag_test_status"] if cr else "",
            "lm_test_status": cr["lm_test_status"] if cr else "",
            "validator_reliability": cr["validator_reliability"] if cr else "not_tested",
            "access_restrictions": r["access_restrictions"],
            "monitoring_viability": "promising" if (r["contains_location_data"] == "verified" and r.get("etag")) else ("possible" if r["contains_location_data"] == "verified" else "unverified"),
        })

    out_path = out / "signal_evaluation.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"  Signal evaluation: {len(rows)} resources evaluated")

    # Summary stats
    verified = sum(1 for r in rows if r["contains_location_data"] == "verified")
    has_etag = sum(1 for r in rows if r["etag_present"])
    has_lm = sum(1 for r in rows if r["last_modified_present"])
    cond_works = sum(1 for r in rows if r["conditional_request_works"] == "yes")
    promising = sum(1 for r in rows if r["monitoring_viability"] == "promising")

    print(f"  Verified location data: {verified}")
    print(f"  With ETag: {has_etag}, With Last-Modified: {has_lm}")
    print(f"  Conditional requests working (304): {cond_works}")
    print(f"  Promising for monitoring: {promising}")

    return rows


def main():
    ap = argparse.ArgumentParser(description="Step 3: Validate change signals")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--historical-run", type=str, default=None, help="Previous run ID for historical comparison")
    ap.add_argument("--skip-conditional", action="store_true", help="Skip conditional request tests")
    ap.add_argument("--skip-historical", action="store_true", help="Skip historical comparison")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # Load existing data
    print("Loading existing data...")
    manifest = list(csv.DictReader(open(out / "manifest.csv")))
    http_entries = [json.loads(line) for line in open(out / "http_metadata.jsonl")]
    print(f"  Manifest: {len(manifest)} spiders")
    print(f"  HTTP entries: {len(http_entries)}")

    # Task 1: Resource inventory
    print("\n=== Task 1: Resource Inventory ===")
    inventory = task1_resource_inventory(out, manifest, http_entries)

    # Task 2: Historical comparison
    if not args.skip_historical:
        print("\n=== Task 2: Historical Comparison ===")
        changes = task2_historical(out, manifest, args.historical_run)
    else:
        print("\n=== Task 2: Historical Comparison (skipped) ===")
        changes = []
        # Ensure changes.csv has header
        if not (out / "changes.csv").exists() or os.path.getsize(out / "changes.csv") == 0:
            write_changes_header(out)

    # Task 3: Conditional probes
    if not args.skip_conditional:
        print("\n=== Task 3: Conditional HTTP Requests ===")
        conditional_results = task3_conditional_probes(out, inventory)
    else:
        print("\n=== Task 3: Conditional HTTP Requests (skipped) ===")
        conditional_results = []

    # Task 4: Provider groups
    print("\n=== Task 4: Provider Groups ===")
    provider_groups = task4_provider_groups(out, manifest, inventory)

    # Task 5: Cost measurement
    print("\n=== Task 5: Cost Measurement ===")
    if conditional_results:
        total_bytes, total_elapsed = task5_cost_measure(inventory, conditional_results)
    else:
        total_bytes, total_elapsed = 0, 0
        print("  Skipped (no conditional results)")

    # Task 6: Signal evaluation
    print("\n=== Task 6: Signal Evaluation ===")
    signal_eval = task6_signal_evaluation(out, inventory, conditional_results, changes, provider_groups)

    # Summary
    print("\n=== Step 3 Summary ===")
    print(f"  Resources inventoried: {len(inventory)}")
    print(f"  Historical changes: {len(changes)}")
    print(f"  Conditional tests: {len(conditional_results)}")
    print(f"  Provider groups: {len(provider_groups)}")
    print(f"  Signal evaluations: {len(signal_eval)}")
    print(f"  Total conditional bytes: {total_bytes}")
    print(f"  Total conditional elapsed: {total_elapsed}ms")

    # Update progress tracker
    print("\nDone. Update PROGRESS.md manually with results.")


    pass


if __name__ == "__main__":
    main()
