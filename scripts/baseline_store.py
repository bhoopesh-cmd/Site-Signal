#!/usr/bin/env python3
"""Baseline store for monitoring: persist and compare resource baselines.

A baseline captures the state of a resource at a point in time:
body hash, normalized fingerprint, validator values, timestamp.
"""
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now_iso():
    return datetime.now(UTC).isoformat()


def load_baselines(out):
    """Load all baselines from baselines.jsonl."""
    path = Path(out) / "baselines.jsonl"
    if not path.exists():
        return []
    baselines = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                baselines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return baselines


def get_latest_baseline(baselines, spider, url):
    """Get the most recent baseline for a resource."""
    matching = [b for b in baselines if b.get("spider") == spider and b.get("resource_url") == url]
    if not matching:
        return None
    matching.sort(key=lambda b: b.get("timestamp", ""), reverse=True)
    return matching[0]


def save_baseline(out, baseline):
    """Append a baseline to baselines.jsonl."""
    path = Path(out) / "baselines.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(baseline) + "\n")


def compute_body_hash(content):
    """Compute SHA-256 hash of response body."""
    if content is None:
        return None
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def compute_fingerprint(features):
    """Compute a normalized fingerprint from a list of GeoJSON features.

    Fingerprint = SHA-256 of sorted (id, name, addr, coords) tuples.
    Returns None if features is empty or None.
    """
    if not features:
        return None
    tuples = []
    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get("properties", {}) or {}
        fid = f.get("id", "")
        name = (props.get("name") or props.get("brand") or "").strip().lower()
        addr = (props.get("addr:street_address") or props.get("addr:full") or "").strip().lower()
        geom = f.get("geometry", {})
        coords = ""
        if isinstance(geom, dict) and geom.get("type") == "Point":
            c = geom.get("coordinates")
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                coords = f"{c[0]:.6f},{c[1]:.6f}"
        tuples.append((fid, name, addr, coords))
    tuples.sort()
    content = json.dumps(tuples, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_sitemap_fingerprint(url_list, lastmod_list=None):
    """Compute fingerprint from sitemap URLs and lastmod dates."""
    if not url_list:
        return None
    pairs = []
    for i, url in enumerate(url_list):
        lm = lastmod_list[i] if lastmod_list and i < len(lastmod_list) else ""
        pairs.append((url, lm or ""))
    pairs.sort()
    content = json.dumps(pairs, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compare_baselines(prev, curr):
    """Compare two baselines. Returns dict with change indicators."""
    result = {
        "body_changed": None,
        "data_changed": None,
        "validator_changed": None,
        "has_prev": prev is not None,
        "has_curr": curr is not None,
    }
    if prev is None or curr is None:
        return result

    # Body hash comparison
    if prev.get("body_hash") and curr.get("body_hash"):
        result["body_changed"] = (prev["body_hash"] != curr["body_hash"])

    # Fingerprint comparison
    if prev.get("normalized_fingerprint") and curr.get("normalized_fingerprint"):
        result["data_changed"] = (prev["normalized_fingerprint"] != curr["normalized_fingerprint"])

    # Validator comparison
    prev_etag = prev.get("etag")
    curr_etag = curr.get("etag")
    prev_lm = prev.get("last_modified")
    curr_lm = curr.get("last_modified")
    if prev_etag or curr_etag or prev_lm or curr_lm:
        result["validator_changed"] = (prev_etag != curr_etag) or (prev_lm != curr_lm)

    return result


def create_baseline(spider, url, body_hash, fingerprint=None, etag=None, last_modified=None, feature_count=0, status="success"):
    """Create a baseline dict."""
    return {
        "baseline_id": str(uuid.uuid4()),
        "spider": spider,
        "resource_url": url,
        "timestamp": now_iso(),
        "body_hash": body_hash,
        "normalized_fingerprint": fingerprint,
        "etag": etag,
        "last_modified": last_modified,
        "feature_count": feature_count,
        "status": status,
    }


def extract_location_features(response_text, spider):
    """Try to extract location features from a JSON response.

    Returns (features_list, fingerprint) or (None, None) if not extractable.
    """
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return None, None

    features = []
    if isinstance(data, dict):
        if "features" in data and isinstance(data["features"], list):
            features = data["features"]
        elif "stores" in data and isinstance(data["stores"], list):
            features = data["stores"]
        elif "data" in data and isinstance(data["data"], list):
            features = data["data"]
        elif "locations" in data and isinstance(data["locations"], list):
            features = data["locations"]
    elif isinstance(data, list):
        features = data

    if not features:
        return None, None

    # Normalize to GeoJSON-like features if needed
    normalized = []
    for f in features:
        if isinstance(f, dict) and "type" in f and f.get("type") == "Feature":
            normalized.append(f)
        elif isinstance(f, dict):
            # Wrap in Feature-like structure
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

    fp = compute_fingerprint(normalized) if normalized else None
    return normalized, fp


def parse_sitemap(url_list, lastmod_list=None):
    """Parse sitemap data into (urls, lastmods, fingerprint)."""
    if not url_list:
        return [], [], None
    fp = compute_sitemap_fingerprint(url_list, lastmod_list)
    return url_list, lastmod_list or [], fp


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Baseline store utilities")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--show", action="store_true", help="Show baseline count")
    args = ap.parse_args()
    baselines = load_baselines(args.output)
    print(f"Baselines: {len(baselines)}")
    if baselines:
        print(f"Latest: {baselines[-1].get('spider')} at {baselines[-1].get('timestamp')}")
