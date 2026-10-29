#!/usr/bin/env python3
"""Collect a reproducible seed dataset from AllThePlaces for ChainXY evaluation.

Usage:
    python3 scripts/collect_seed.py --sample-size 100 --output data/seed
    python3 scripts/collect_seed.py --sample-size 100 --output data/seed --historical
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

TOKEN_RE = re.compile(r"(token|key|secret|password|auth|session|sid|api_key|apikey)=[^&]+", re.I)


def redact_url(url):
    if not url:
        return url
    return TOKEN_RE.sub(r"\1=REDACTED", url)


LATEST_URL = "https://data.alltheplaces.xyz/runs/latest.json"
GITHUB_RAW = "https://raw.githubusercontent.com/alltheplaces/alltheplaces/master/{}"
USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"

COUNTRY_RE = re.compile(r"_([a-z]{2})(?:_|$)", re.I)
INDUSTRY_KEYWORDS = [
    ("bank", "banking"), ("pizza", "food"), ("coffee", "food"), ("burger", "food"),
    ("restaurant", "food"), ("cafe", "food"), ("food", "food"), ("grocery", "retail"),
    ("supermarket", "retail"), ("shop", "retail"), ("store", "retail"), ("retail", "retail"),
    ("hotel", "hospitality"), ("motel", "hospitality"), ("pharmacy", "health"),
    ("hospital", "health"), ("clinic", "health"), ("auto", "automotive"), ("car", "automotive"),
    ("fuel", "fuel"), ("gas", "fuel"), ("petrol", "fuel"), ("oil", "fuel"),
    ("fashion", "fashion"), ("clothing", "fashion"), ("apparel", "fashion"),
    ("hardware", "home"), ("furniture", "home"), ("home", "home"),
    ("library", "public"), ("school", "education"), ("university", "education"),
    ("insurance", "finance"), ("finance", "finance"),
    ("post", "postal"), ("courier", "postal"), ("parcel", "postal"), ("locker", "postal"),
]


def log(msg):
    print(msg, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url, timeout=30):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.json(), r


def validate_geojson(data):
    if not isinstance(data, dict):
        return False, "not a dict"
    if data.get("type") != "FeatureCollection":
        return False, "type != FeatureCollection"
    if not isinstance(data.get("features"), list):
        return False, "features not a list"
    return True, "ok"


def country_bucket(spider):
    m = COUNTRY_RE.search(spider)
    if m:
        return m.group(1).lower()
    # try last underscore token
    parts = spider.split("_")
    if parts and len(parts[-1]) == 2 and parts[-1].isalpha():
        return parts[-1].lower()
    return "global"


def industry_bucket(spider):
    s = spider.lower()
    for kw, ind in INDUSTRY_KEYWORDS:
        if kw in s:
            return ind
    return "other"


def size_bucket(n):
    if n < 50:
        return "small"
    if n <= 500:
        return "medium"
    return "large"


def fetch_spider_source(filename, timeout=30):
    url = GITHUB_RAW.format(filename)
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if r.status_code == 200:
            return r.text, url, "success"
        return None, url, f"http_{r.status_code}"
    except Exception as e:
        return None, url, f"error: {e}"


def resolve_sitemap_pointer(url, timeout=10):
    """If url is a robots.txt sitemap pointer or sitemap index, resolve to actual sitemap URL."""
    if not url:
        return url
    # If it's a sitemap index, fetch and get first child sitemap
    if url.lower().endswith("sitemap_index.xml") or url.lower().endswith("sitemap.xml"):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
            if r.status_code == 200:
                # Check if it's a sitemap index (contains <sitemap> entries)
                if "<sitemap>" in r.text.lower() or "<sitemap " in r.text.lower():
                    # Extract first child sitemap
                    import re as _re
                    m = _re.search(r"<loc>\s*(https?://[^<]+)\s*</loc>", r.text, _re.I)
                    if m:
                        return m.group(1).strip()
                # If it's a regular sitemap with <url> entries, it's already the right resource
                if "<url>" in r.text.lower() or "<url " in r.text.lower():
                    return url
        except Exception:
            pass
        return url
    # If it's robots.txt, fetch and extract Sitemap: directive
    if url.lower().endswith("robots.txt"):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("sitemap:"):
                        sitemap_url = line.split(":", 1)[1].strip() if ":" in line else ""
                        if sitemap_url.startswith("http"):
                            # Recursively resolve if it's a sitemap index
                            return resolve_sitemap_pointer(sitemap_url, timeout=timeout)
        except Exception:
            pass
    return url  # fallback to original


def analyze_source(source_text):
    info = {
        "source_urls": [],
        "rendering": "static",
        "extraction_method": "unknown",
        "platform": "none",
        "source_mapping_confidence": "low",
    }
    if not source_text:
        return info
    # start_urls extraction
    urls = set()
    for m in re.finditer(r'start_urls\s*[:=].*?\[([^\]]*)\]', source_text, re.S):
        for um in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            urls.add(um.group(1))
    # capture all quoted http(s) urls, excluding docs/github/non-source
    EXCLUDE_DOMAINS = ["github.com", "raw.githubusercontent", "pypi.org", "alltheplaces", "openaddresses", "wikidata", "osm.org", "wikipedia", "example.com"]
    for m in re.finditer(r'["\'](https?://[^"\']+)["\']', source_text):
        u = m.group(1)
        if any(d in u.lower() for d in EXCLUDE_DOMAINS):
            continue
        urls.add(u)
    # Resolve sitemap pointers (robots.txt -> actual sitemap URL)
    resolved_urls = set()
    for u in urls:
        resolved = resolve_sitemap_pointer(u)
        resolved_urls.add(resolved)
    urls = resolved_urls
    info["source_urls"] = sorted(urls)[:5]
    # rendering detection
    low = source_text.lower()
    if any(k in low for k in ["zyte_api", "browserhtml", "browser_html", "playwright", "selenium", "render"]):
        info["rendering"] = "js"
    # POST detection
    if "FormRequest" in source_text or "requests.post" in low or 'method="POST"' in source_text or "method='POST'" in source_text:
        info["http_method"] = "POST"
    else:
        info["http_method"] = "GET"
    # extraction method
    if "KiboSpider" in source_text:
        info["extraction_method"] = "kibo_storefinder"
    elif "DictParser" in source_text:
        info["extraction_method"] = "dict_parser"
    elif ".json()" in source_text or "response.json" in source_text:
        info["extraction_method"] = "json_api"
    elif "graphql" in low:
        info["extraction_method"] = "graphql"
    elif "SitemapSpider" in source_text:
        info["extraction_method"] = "sitemap_crawl"
    else:
        info["extraction_method"] = "html_scrape"
    # platform
    for plat in ["kibo", "yext", "storemapper", "storepoint", "stockist", "mapbox", "google", "arcgis", "algolia"]:
        if plat in low:
            info["platform"] = plat
            break
    if info["source_urls"]:
        info["source_mapping_confidence"] = "high" if len(info["source_urls"]) >= 1 else "medium"
    else:
        info["source_mapping_confidence"] = "low"
    return info


def domain_of(url):
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def probe_http(urls, timeout=15, method="GET", headers_extra=None):
    results = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        entry = {
            "url": redact_url(u) if "redact_url" in globals() else u,
            "timestamp": now_iso(),
            "status": None,
            "final_url": None,
            "etag": None,
            "last_modified": None,
            "cache_control": None,
            "content_length": None,
            "content_type": None,
            "conditional_feasible": False,
            "etag_strength": "none",
            "contains_location_data_directly": "unknown",
            "error": None,
            "bytes_transferred": 0,
            "elapsed_ms": 0,
            "http_method": method,
        }
        # Redact URL
        try:
            entry["url"] = TOKEN_RE.sub(r"\1=REDACTED", u) if "TOKEN_RE" in globals() else u
        except Exception:
            pass
        t0 = time.time()
        retries = 0
        max_retries = 3
        while retries <= max_retries:
            try:
                hdrs = {"User-Agent": USER_AGENT}
                if headers_extra:
                    hdrs.update(headers_extra)
                if method == "POST":
                    r = requests.post(u, timeout=timeout, headers=hdrs, allow_redirects=True)
                else:
                    r = requests.get(u, timeout=timeout, headers=hdrs, allow_redirects=True)
                entry["elapsed_ms"] = int((time.time() - t0) * 1000)
                entry["status"] = r.status_code
                entry["final_url"] = r.url
                h = r.headers
                entry["etag"] = h.get("ETag")
                entry["last_modified"] = h.get("Last-Modified")
                entry["cache_control"] = h.get("Cache-Control")
                entry["content_length"] = h.get("Content-Length")
                entry["content_type"] = h.get("Content-Type")
                entry["bytes_transferred"] = len(r.content) if r.content else 0
                entry["conditional_feasible"] = bool(entry["etag"] or entry["last_modified"])
                # Weak ETag detection
                if entry["etag"] and entry["etag"].startswith('W/"'):
                    entry["etag_strength"] = "weak"
                elif entry["etag"]:
                    entry["etag_strength"] = "strong"
                else:
                    entry["etag_strength"] = "none"
                ct = (entry["content_type"] or "").lower()
                if "json" in ct or "geojson" in ct:
                    entry["contains_location_data_directly"] = "likely"
                elif "html" in ct:
                    entry["contains_location_data_directly"] = "maybe (html)"
                elif "xml" in ct:
                    entry["contains_location_data_directly"] = "maybe (xml)"
                else:
                    entry["contains_location_data_directly"] = "unknown"
                # Rate limit handling
                if r.status_code == 429 or r.status_code == 503:
                    retry_after = r.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else (2 ** retries)
                    if retries < max_retries:
                        time.sleep(min(wait, 30))
                        retries += 1
                        continue
                break
            except Exception as e:
                entry["error"] = str(e)
                entry["elapsed_ms"] = int((time.time() - t0) * 1000)
                retries += 1
                if retries <= max_retries:
                    time.sleep(2 ** retries)
                    continue
                break
        # Redact final_url
        if entry["final_url"]:
            entry["final_url"] = TOKEN_RE.sub(r"\1=REDACTED", entry["final_url"]) if "TOKEN_RE" in globals() else entry["final_url"]
        results.append(entry)
    return results


def normalize_feature(feat, spider, run_id, retrieval_ts, raw_path):
    props = feat.get("properties") or {}
    geom = feat.get("geometry")
    coords = None
    if isinstance(geom, dict):
        gtype = geom.get("type")
        # Only derive lat/lon from Point geometry. For non-Point geometries
        # (Polygon, LineString, etc.), preserve the original geometry as-is
        # and do not fabricate a representative point — a polygon vertex is
        # not necessarily a store location.
        if gtype == "Point":
            c = geom.get("coordinates")
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                coords = (c[0], c[1])
        elif gtype == "MultiPoint":
            c = geom.get("coordinates")
            if isinstance(c, (list, tuple)) and len(c) > 0 and isinstance(c[0], (list, tuple)) and len(c[0]) >= 2:
                coords = (c[0][0], c[0][1])
    addr = {}
    for k in ["street_address", "street", "city", "state", "postcode", "country", "full", "housenumber", "unit"]:
        v = props.get(f"addr:{k}")
        if v is not None:
            addr[k] = v
    # flatten address to top-level normalized keys
    street_address = props.get("addr:street_address") or props.get("addr:street")
    city = props.get("addr:city")
    state = props.get("addr:state")
    postcode = props.get("addr:postcode")
    country = props.get("addr:country")
    full_addr = props.get("addr:full")
    name = props.get("name") or props.get("brand")
    brand = props.get("brand")
    ref = props.get("ref")
    phone = props.get("phone")
    email = props.get("email")
    website = props.get("website")
    opening_hours = props.get("opening_hours")
    nsi_id = props.get("nsi_id")
    wikidata = props.get("brand:wikidata") or props.get("operator:wikidata")
    source_uri = props.get("@source_uri")
    feat_id = feat.get("id")
    lat = coords[1] if coords else None
    lon = coords[0] if coords else None
    geom_type = geom.get("type") if isinstance(geom, dict) else None
    norm = {
        "id": feat_id,
        "spider": spider,
        "ref": ref,
        "name": name,
        "brand": brand,
        "phone": phone,
        "email": email,
        "website": website,
        "opening_hours": opening_hours,
        "nsi_id": nsi_id,
        "wikidata": wikidata,
        "source_uri": source_uri,
        "addr_street_address": street_address,
        "addr_city": city,
        "addr_state": state,
        "addr_postcode": postcode,
        "addr_country": country,
        "addr_full": full_addr,
        "geom_type": geom_type,
        "lon": lon,
        "lat": lat,
        "run_id": run_id,
        "retrieval_timestamp": retrieval_ts,
        "raw_file": raw_path,
    }
    # attach provenance to feature
    feat_out = json.loads(json.dumps(feat))  # deep copy
    if feat_out.get("properties") is None:
        feat_out["properties"] = {}
    feat_out.setdefault("properties", {})
    feat_out["properties"]["provenance"] = {
        "spider": spider,
        "run_id": run_id,
        "retrieval_timestamp": retrieval_ts,
        "original_feature_id": feat_id,
        "raw_file": raw_path,
    }
    return norm, feat_out


def main():
    ap = argparse.ArgumentParser(description="Collect ChainXY seed dataset from AllThePlaces")
    ap.add_argument("--sample-size", type=int, default=100)
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-features", type=int, default=5)
    ap.add_argument("--historical", action="store_true", help="Attempt historical comparison (downloads ~2.5GB archive)")
    ap.add_argument("--freshness-threshold-days", type=int, default=14)
    args = ap.parse_args()

    out = Path(args.output)
    raw_dir = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    import random
    random.seed(args.seed)

    # 1. Fetch latest run metadata
    log("Fetching latest run metadata...")
    try:
        latest_meta, latest_resp = fetch_json(LATEST_URL, timeout=args.timeout)
    except Exception as e:
        log(f"FATAL: cannot fetch latest.json: {e}")
        sys.exit(1)

    run_id = latest_meta.get("run_id")
    stats_url = latest_meta.get("stats_url")
    end_time = latest_meta.get("end_time") or latest_meta.get("start_time")
    log(f"Latest run: {run_id}, end_time: {end_time}")

    # Freshness check
    freshness_status = "fresh"
    history_newest_id = None
    history_newest_start = None
    try:
        history, _ = fetch_json(HISTORY_URL, timeout=args.timeout)
        # find newest by start_time
        best = None
        for h in history:
            st = h.get("start_time")
            if st and (best is None or st > best.get("start_time", "")):
                best = h
        if best:
            history_newest_id = best.get("run_id")
            history_newest_start = best.get("start_time")
        if history_newest_id and history_newest_id != run_id:
            freshness_status = "discrepancy: history newer than latest.json"
            log(f"WARNING: history newest run ({history_newest_id}) != latest.json ({run_id})")
        # threshold check
        if end_time:
            try:
                et = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - et).days
                if age_days > args.freshness_threshold_days:
                    freshness_status = f"stale: latest run is {age_days} days old"
                    log(f"WARNING: latest run is {age_days} days old (threshold {args.freshness_threshold_days})")
            except Exception:
                pass
    except Exception as e:
        log(f"WARNING: cannot fetch history for freshness check: {e}")
        freshness_status = f"unknown: {e}"

    if not stats_url:
        log("FATAL: no stats_url in latest metadata")
        sys.exit(1)

    # 2. Fetch stats
    log("Fetching stats...")
    try:
        stats_raw, _ = fetch_json(stats_url, timeout=args.timeout)
    except Exception as e:
        log(f"FATAL: cannot fetch stats: {e}")
        sys.exit(1)

    # defensive parse
    log(f"Stats top-level keys: {list(stats_raw.keys()) if isinstance(stats_raw, dict) else type(stats_raw)}")
    results_list = []
    if isinstance(stats_raw, dict):
        results_list = stats_raw.get("results") or stats_raw.get("data") or []
    elif isinstance(stats_raw, list):
        results_list = stats_raw
    log(f"Stats results count: {len(results_list)}")
    if results_list:
        log(f"Sample result keys: {list(results_list[0].keys())}")

    # 3. Qualify spiders
    qualified = []
    for entry in results_list:
        if not isinstance(entry, dict):
            continue
        spider = entry.get("spider") or entry.get("name") or entry.get("spider_name")
        if not spider:
            continue
        filename = entry.get("filename") or entry.get("file") or entry.get("path")
        errors = entry.get("errors")
        features = entry.get("features")
        # exclude infrastructure/addresses
        fn = (filename or "").lower()
        if "infrastructure" in fn or "addresses" in fn or "address" in fn:
            continue
        if "infrastructure" in spider.lower() or spider.lower().endswith("_addresses"):
            continue
        # errors check (if present)
        if errors is not None and errors != 0:
            # allow small errors? no, require 0
            if isinstance(errors, int) and errors > 0:
                continue
        # features check
        if features is not None:
            try:
                if int(features) < args.min_features:
                    continue
            except (ValueError, TypeError):
                pass
        qualified.append({
            "spider": spider,
            "filename": filename,
            "errors": errors,
            "features": features if features is not None else 0,
        })

    log(f"Qualified spiders: {len(qualified)}")

    # 4. Stratified sampling
    # group by (country_bucket, size_bucket)
    groups = {}
    for q in qualified:
        try:
            n = int(q["features"]) if q["features"] is not None else 0
        except (ValueError, TypeError):
            n = 0
        key = (country_bucket(q["spider"]), size_bucket(n))
        groups.setdefault(key, []).append(q)

    # round-robin across groups, preferring underrepresented
    selected = []
    selected_names = set()
    # sort groups by size (smallest first to ensure coverage)
    group_keys = sorted(groups.keys(), key=lambda k: len(groups[k]))
    # iterate rounds
    round_idx = 0
    while len(selected) < args.sample_size:
        progressed = False
        for gk in group_keys:
            if len(selected) >= args.sample_size:
                break
            members = [m for m in groups[gk] if m["spider"] not in selected_names]
            if round_idx < len(members):
                pick = members[round_idx]
                selected.append(pick)
                selected_names.add(pick["spider"])
                progressed = True
        round_idx += 1
        if not progressed:
            break

    # if still short, fill from remaining
    if len(selected) < args.sample_size:
        remaining = [q for q in qualified if q["spider"] not in selected_names]
        random.shuffle(remaining)
        for q in remaining:
            if len(selected) >= args.sample_size:
                break
            selected.append(q)
            selected_names.add(q["spider"])

    log(f"Selected {len(selected)} spiders (target {args.sample_size})")

    # 5. Dedup domains: group selected by brand family (domain from source, filled later)
    # We'll compute related spiders after source analysis.

    # 6. Per-spider pipeline
    retrieval_ts = now_iso()
    spider_results = {}  # spider -> dict
    all_features_out = []
    http_metadata_lines = []
    normalized_records = []

    def process_spider(q):
        spider = q["spider"]
        filename = q["filename"]
        try:
            n_feat = int(q["features"]) if q["features"] is not None else 0
        except (ValueError, TypeError):
            n_feat = 0
        res = {
            "spider": spider,
            "brand": spider.split("_")[0],
            "category": industry_bucket(spider),
            "country": country_bucket(spider),
            "country_count": 0,
            "domain": "",
            "source_url": "",
            "store_locator_url": "",
            "api_endpoints": "",
            "rendering_required": "unknown",
            "extraction_method": "unknown",
            "http_method": "GET",
            "source_mapping_confidence": "low",
            "run_id": run_id,
            "collection_time": retrieval_ts,
            "feature_count": n_feat,
            "fields_present": "",
            "monitoring_strategy_candidate": "polling",
            "http_status": "",
            "http_etag": "",
            "http_last_modified": "",
            "collection_status": "pending",
            "related_spiders": "",
            "source_file": filename or "",
            "source_fetch_status": "",
            "geojson_fetch_status": "",
            "geojson_path": "",
        }
        # download geojson
        spider_raw_dir = raw_dir / spider
        spider_raw_dir.mkdir(parents=True, exist_ok=True)
        geojson_path = spider_raw_dir / f"{run_id}.geojson"
        res["geojson_path"] = str(geojson_path)
        geojson_data = None
        if geojson_path.exists():
            try:
                with open(geojson_path) as f:
                    geojson_data = json.load(f)
                ok, msg = validate_geojson(geojson_data)
                if ok:
                    res["geojson_fetch_status"] = "cached"
                else:
                    res["geojson_fetch_status"] = f"cached_invalid: {msg}"
                    geojson_data = None
            except Exception as e:
                res["geojson_fetch_status"] = f"cached_error: {e}"
                geojson_data = None
        if geojson_data is None:
            url = f"https://data.alltheplaces.xyz/runs/latest/output/{spider}.geojson"
            try:
                r = requests.get(url, timeout=args.timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
                if r.status_code == 200:
                    try:
                        geojson_data = r.json()
                    except Exception:
                        geojson_data = None
                    ok, msg = validate_geojson(geojson_data) if geojson_data else (False, "no data")
                    if ok:
                        with open(geojson_path, "w") as f:
                            json.dump(geojson_data, f)
                        res["geojson_fetch_status"] = "success"
                    else:
                        res["geojson_fetch_status"] = f"invalid: {msg}"
                        geojson_data = None
                else:
                    res["geojson_fetch_status"] = f"http_{r.status_code}"
            except Exception as e:
                res["geojson_fetch_status"] = f"error: {e}"

        # fetch source
        source_text = None
        source_url = ""
        if filename:
            source_text, source_url, src_status = fetch_spider_source(filename, timeout=args.timeout)
            res["source_fetch_status"] = src_status
        else:
            res["source_fetch_status"] = "no_filename"

        analysis = analyze_source(source_text or "")
        res["rendering_required"] = analysis["rendering"]
        res["extraction_method"] = analysis["extraction_method"]
        res["http_method"] = analysis.get("http_method", "GET")
        res["source_mapping_confidence"] = analysis["source_mapping_confidence"]
        res["platform"] = analysis.get("platform", "none")
        urls = analysis["source_urls"]
        if urls:
            res["source_url"] = urls[0]
            res["domain"] = domain_of(urls[0])
            res["store_locator_url"] = urls[0]
            res["api_endpoints"] = ";".join(urls[1:]) if len(urls) > 1 else urls[0]
            res["monitoring_strategy_candidate"] = "conditional_request" if True else "polling"

        # HTTP probe representative URLs
        probe_urls = urls[:3] if urls else []
        # dedup
        probe_urls = list(dict.fromkeys(probe_urls))
        http_method = analysis.get("http_method", "GET")
        http_entries = probe_http(probe_urls, timeout=min(args.timeout, 15), method=http_method)
        for he in http_entries:
            http_metadata_lines.append({
                "spider": spider,
                "run_id": run_id,
                "retrieval_timestamp": retrieval_ts,
                **he,
            })
        if http_entries:
            first = http_entries[0]
            res["http_status"] = first.get("status")
            res["http_etag"] = first.get("etag")
            res["http_etag_strength"] = first.get("etag_strength", "none")
            res["http_last_modified"] = first.get("last_modified")

        # normalize features
        fields_set = set()
        brand_from_data = None
        if geojson_data and isinstance(geojson_data, dict):
            feats = geojson_data.get("features") or []
            res["feature_count"] = len(feats)
            for feat in feats:
                if not isinstance(feat, dict):
                    continue
                props = feat.get("properties") or {}
                for k in props.keys():
                    fields_set.add(k)
                if brand_from_data is None:
                    brand_from_data = props.get("brand")
                norm, feat_out = normalize_feature(feat, spider, run_id, retrieval_ts, str(geojson_path))
                normalized_records.append(norm)
                all_features_out.append(feat_out)
        if brand_from_data:
            res["brand"] = brand_from_data
        # Count unique countries
        countries = set()
        for feat in (geojson_data.get("features", []) if geojson_data and isinstance(geojson_data, dict) else []):
            props = feat.get("properties", {}) or {}
            c = props.get("addr:country")
            if c:
                countries.add(c)
        if countries:
            res["country_count"] = len(countries)
        res["fields_present"] = ";".join(sorted(fields_set))

        # collection status
        if res["geojson_fetch_status"] in ("success", "cached") and geojson_data is not None:
            res["collection_status"] = "success"
        elif geojson_data is not None:
            res["collection_status"] = "partial"
        else:
            res["collection_status"] = "failed"

        return res

    log("Processing spiders...")
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(process_spider, q): q for q in selected}
        done = 0
        for fut in as_completed(futures):
            try:
                r = fut.result()
                spider_results[r["spider"]] = r
            except Exception as e:
                q = futures[fut]
                log(f"ERROR processing {q['spider']}: {e}")
                spider_results[q["spider"]] = {"spider": q["spider"], "collection_status": "failed", "error": str(e)}
            done += 1
            if done % 10 == 0 or done == len(selected):
                log(f"Processed {done}/{len(selected)}")

    # 7. Dedup domains: group selected by domain
    domain_to_spiders = {}
    for sp, r in spider_results.items():
        d = r.get("domain") or ""
        if d:
            domain_to_spiders.setdefault(d, []).append(sp)
    for sp, r in spider_results.items():
        d = r.get("domain") or ""
        if d and len(domain_to_spiders.get(d, [])) > 1:
            r["related_spiders"] = ";".join(s for s in domain_to_spiders[d] if s != sp)
            r["monitoring_strategy_candidate"] = "shared_domain"

    # 8. Write manifest.csv
    log("Writing manifest.csv...")
    manifest_cols = [
        "spider", "brand", "category", "country", "country_count", "domain", "source_url", "store_locator_url",
        "api_endpoints", "rendering_required", "extraction_method", "platform", "http_method",
        "source_mapping_confidence", "run_id", "collection_time", "feature_count",
        "fields_present", "monitoring_strategy_candidate", "http_status", "http_etag",
        "http_etag_strength", "http_last_modified", "collection_status", "related_spiders", "source_file",
        "source_fetch_status", "geojson_fetch_status",
    ]
    manifest_path = out / "manifest.csv"
    with open(manifest_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=manifest_cols, extrasaction="ignore")
        w.writeheader()
        for sp in sorted(spider_results.keys()):
            r = spider_results[sp]
            w.writerow({c: r.get(c, "") for c in manifest_cols})

    # 9. Write locations.geojson
    log("Writing locations.geojson...")
    locations_path = out / "locations.geojson"
    fc = {"type": "FeatureCollection", "features": all_features_out}
    fc["dataset_attributes"] = {
        "run_id": run_id,
        "collection_time": retrieval_ts,
        "spider_count": len(selected),
        "feature_count": len(all_features_out),
    }
    with open(locations_path, "w") as f:
        json.dump(fc, f)

    # 10. Write http_metadata.jsonl
    log("Writing http_metadata.jsonl...")
    http_path = out / "http_metadata.jsonl"
    with open(http_path, "w") as f:
        for line in http_metadata_lines:
            f.write(json.dumps(line) + "\n")

    # 11. changes.csv (header; historical optional)
    changes_path = out / "changes.csv"
    changes_cols = ["spider", "change_type", "record_id", "field", "old_value", "new_value",
                    "matching_confidence", "resolved", "notes"]
    if args.historical:
        log("Historical mode requested. Attempting to download previous run archive...")
        # find previous run ~30 days before
        prev_run = None
        try:
            history, _ = fetch_json(HISTORY_URL, timeout=args.timeout)
            # sort by start_time desc
            history_sorted = sorted(history, key=lambda h: h.get("start_time", ""), reverse=True)
            # pick one that is not the latest and has output_url
            for h in history_sorted:
                if h.get("run_id") == run_id:
                    continue
                if h.get("output_url"):
                    prev_run = h
                    break
        except Exception as e:
            log(f"Cannot fetch history: {e}")
        if prev_run:
            prev_run_id = prev_run["run_id"]
            output_url = prev_run["output_url"]
            zip_path = raw_dir / "_history" / f"{prev_run_id}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            extracted_dir = raw_dir / "_history" / prev_run_id
            # download if not exists
            if not zip_path.exists():
                log(f"Downloading {output_url} (may be large)...")
                try:
                    with requests.get(output_url, stream=True, timeout=args.timeout, headers={"User-Agent": USER_AGENT}) as r:
                        r.raise_for_status()
                        with open(zip_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                    log("Download complete.")
                except Exception as e:
                    log(f"Historical download failed: {e}")
                    prev_run = None
            if prev_run and zip_path.exists():
                try:
                    if not extracted_dir.exists():
                        extracted_dir.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(zip_path) as z:
                            z.extractall(extracted_dir)
                    log("Extracted. Comparing...")
                    # build lookup of current features by spider
                    current_by_spider = {}
                    for rec in normalized_records:
                        current_by_spider.setdefault(rec["spider"], {}).setdefault(rec["id"], rec)
                    # for each selected spider, find historical geojson
                    rows = []
                    for sp in spider_results:
                        hist_file = None
                        for root, _, files in os.walk(extracted_dir):
                            for fn in files:
                                if fn == f"{sp}.geojson":
                                    hist_file = os.path.join(root, fn)
                                    break
                            if hist_file:
                                break
                        if not hist_file:
                            continue
                        try:
                            with open(hist_file) as f:
                                hist_data = json.load(f)
                        except Exception:
                            continue
                        hist_feats = hist_data.get("features", []) if isinstance(hist_data, dict) else []
                        hist_ids = set()
                        hist_map = {}
                        for hf in hist_feats:
                            if not isinstance(hf, dict):
                                continue
                            hid = hf.get("id")
                            hist_ids.add(hid)
                            hist_map[hid] = hf
                        cur_ids = set(current_by_spider.get(sp, {}).keys())
                        # added
                        for cid in cur_ids - hist_ids:
                            rows.append({"spider": sp, "change_type": "added", "record_id": cid,
                                         "field": "", "old_value": "", "new_value": "present",
                                         "matching_confidence": "high", "resolved": "yes",
                                         "notes": "id in current not in historical"})
                        # removed
                        for hid in hist_ids - cur_ids:
                            rows.append({"spider": sp, "change_type": "removed", "record_id": hid,
                                         "field": "", "old_value": "present", "new_value": "",
                                         "matching_confidence": "high", "resolved": "yes",
                                         "notes": "id in historical not in current"})
                        # changed: compare matching ids
                        for mid in cur_ids & hist_ids:
                            cf = None
                            # find current feature
                            for feat in all_features_out:
                                if feat.get("id") == mid and (feat.get("properties", {}).get("@spider") == sp or feat.get("properties", {}).get("provenance", {}).get("spider") == sp):
                                    cf = feat
                                    break
                            hf = hist_map.get(mid)
                            if cf is None or hf is None:
                                continue
                            cprops = cf.get("properties", {})
                            hprops = hf.get("properties", {}) if isinstance(hf, dict) else {}
                            # compare a few fields
                            for field in ["addr:street_address", "addr:city", "addr:state", "addr:postcode", "name", "phone"]:
                                cv = cprops.get(field)
                                hv = hprops.get(field)
                                if cv is not None and hv is not None and cv != hv:
                                    rows.append({"spider": sp, "change_type": f"changed_{field}", "record_id": mid,
                                                 "field": field, "old_value": str(hv), "new_value": str(cv),
                                                 "matching_confidence": "high", "resolved": "yes",
                                                 "notes": "field differs"})
                            # coords
                            cgeom = cf.get("geometry", {})
                            hgeom = hf.get("geometry", {}) if isinstance(hf, dict) else {}
                            cc = cgeom.get("coordinates") if isinstance(cgeom, dict) else None
                            hc = hgeom.get("coordinates") if isinstance(hgeom, dict) else None
                            if cc and hc and cc != hc:
                                rows.append({"spider": sp, "change_type": "changed_coordinates", "record_id": mid,
                                             "field": "coordinates", "old_value": str(hc), "new_value": str(cc),
                                             "matching_confidence": "high", "resolved": "yes",
                                             "notes": "coordinates differ"})
                    with open(changes_path, "w", newline="") as f:
                        w = csv.DictWriter(f, fieldnames=changes_cols)
                        w.writeheader()
                        for row in rows:
                            w.writerow(row)
                    log(f"Wrote {len(rows)} candidate changes.")
                except Exception as e:
                    log(f"Historical comparison failed: {e}")
        else:
            log("No previous run with output_url found or download skipped.")
            # write header only
            with open(changes_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=changes_cols)
                w.writeheader()
    else:
        log("Historical comparison deferred (use --historical to attempt). Writing changes.csv header only.")
        with open(changes_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=changes_cols)
            w.writeheader()

    # 12. collection_report.md
    log("Writing collection_report.md...")
    success_count = sum(1 for r in spider_results.values() if r.get("collection_status") == "success")
    failed_count = sum(1 for r in spider_results.values() if r.get("collection_status") == "failed")
    total_features = len(all_features_out)
    http_ok = sum(1 for h in http_metadata_lines if h.get("status") and 200 <= h["status"] < 400)
    etag_count = sum(1 for h in http_metadata_lines if h.get("etag"))
    lm_count = sum(1 for h in http_metadata_lines if h.get("last_modified"))

    report_path = out / "collection_report.md"
    with open(report_path, "w") as f:
        f.write("# Collection Report\n\n")
        f.write(f"- Collection timestamp: {retrieval_ts}\n")
        f.write(f"- Run ID: {run_id}\n")
        f.write(f"- Run end_time: {end_time}\n")
        f.write(f"- Freshness status: {freshness_status}\n")
        f.write(f"- History newest run_id: {history_newest_id} (start: {history_newest_start})\n")
        f.write(f"- Sample size target: {args.sample_size}\n")
        f.write(f"- Spiders selected: {len(selected)}\n")
        f.write(f"- Successful current outputs: {success_count}\n")
        f.write(f"- Failed/incomplete: {failed_count}\n")
        f.write(f"- Total reference features: {total_features}\n")
        f.write(f"- HTTP resources probed: {len(http_metadata_lines)}\n")
        f.write(f"- HTTP probes successful (2xx/3xx): {http_ok}\n")
        f.write(f"- Resources with ETag: {etag_count}\n")
        f.write(f"- Resources with Last-Modified: {lm_count}\n")
        f.write(f"- Historical comparison: {'attempted' if args.historical else 'deferred (use --historical)'}\n\n")
        f.write("## Sampling methodology\n")
        f.write("Automated stratified sampling over qualifying spiders (errors==0, features>={}, not infrastructure/addresses). ".format(args.min_features))
        f.write("Strata: country bucket (from spider name), size bucket (small<50, medium 50-500, large>500), industry bucket (keyword from spider name). ")
        f.write("Deterministic round-robin selection with seed={}.\n\n".format(args.seed))
        f.write("## Limitations\n")
        f.write("- AllThePlaces is reference data, not guaranteed ground truth.\n")
        f.write("- Source mapping is heuristic (regex over spider source code); confidence reflects evidence.\n")
        f.write("- HTTP metadata is baseline only; ETag/Last-Modified reliability not asserted.\n")
        f.write("- Historical comparison deferred by default due to ~2.5GB archive size.\n")
        f.write("- Some spiders may be inaccessible; failures documented in manifest.\n\n")
        f.write("## Failed/incomplete collections\n")
        for sp, r in spider_results.items():
            if r.get("collection_status") != "success":
                f.write(f"- {sp}: {r.get('collection_status')} ({r.get('geojson_fetch_status')})\n")

    log("Done.")
    log(f"Selected: {len(selected)}, success: {success_count}, features: {total_features}, http: {len(http_metadata_lines)}")


# --- Step 2: probe diagnostics helpers (shared with diagnose_probes.py) ---

def extract_request_urls(source_text):
    """Extract actual request URLs from spider source using multiple patterns."""
    urls = []
    for m in re.finditer(r"start_urls\s*[:=].*?\[([^\]]*)\]", source_text, re.S):
        for um in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            urls.append(("start_urls", um.group(1)))
    for m in re.finditer(r"sitemap_urls\s*[:=].*?\[([^\]]*)\]", source_text, re.S):
        for um in re.finditer(r'["\']([^"\']+)["\']', m.group(1)):
            urls.append(("sitemap_urls", um.group(1)))
    for m in re.finditer(r"(?:JsonRequest|FormRequest|Request)\(\s*[\"']([^\"']+)[\"']", source_text):
        urls.append(("request_call", m.group(1)))
    for m in re.finditer(r"(?:JsonRequest|FormRequest|Request)\(\s*f[\"']([^\"']+)[\"']", source_text):
        urls.append(("fstring_request", m.group(1)))
    for m in re.finditer(r"urljoin\([^,]+,\s*[\"']([^\"']+)[\"']", source_text):
        urls.append(("urljoin", m.group(1)))
    for m in re.finditer(r"(\w+)\s*=\s*f[\"'](https?://[^\"']+)[\"']", source_text):
        urls.append(("fstring_var", m.group(2)))
    EXCLUDE = ["github.com", "raw.githubusercontent", "pypi.org", "alltheplaces", "openaddresses", "wikidata", "osm.org", "wikipedia", "example.com"]
    for m in re.finditer(r'["\'](https?://[^"\']+)["\']', source_text):
        u = m.group(1)
        if any(d in u.lower() for d in EXCLUDE):
            continue
        urls.append(("quoted_url", u))
    return urls


def classify_failure(spider, url, status, error, source_urls):
    """Classify a failed probe based on URL and source evidence."""
    if not url or not str(url).startswith("http"):
        if "class" in str(url) or "xpath" in str(url).lower() or str(url).startswith("//*"):
            return "Incorrect or irrelevant URL"
        if str(url).startswith("?"):
            return "Missing request parameters"
        return "Incorrect or irrelevant URL"
    if "{" in url or "}" in url or "[" in url:
        return "Missing request parameters"
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
    if error and ("DNS" in str(error) or "resolve" in str(error).lower() or "NameResolution" in str(error) or "Connection" in str(error)):
        return "Network or DNS failure"
    if error:
        return "Network or DNS failure"
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


if __name__ == "__main__":
    main()
