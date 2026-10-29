#!/usr/bin/env python3
"""Step 3 Task 3: Repeatable live-observation process.

Captures one observation of all verified resources and appends to observations.jsonl.
Usage:
    python3 scripts/live_observe.py --output data/seed --label "baseline"
    python3 scripts/live_observe.py --output data/seed --label "week2" --min-interval-hours 24
"""
import argparse
import csv
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_seed as cs

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"
TOKEN_RE = cs.TOKEN_RE


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def redact_url(url):
    return cs.redact_url(url)


def load_observations(out):
    """Load existing observations from observations.jsonl."""
    obs_path = out / "observations.jsonl"
    if not obs_path.exists():
        return []
    obs = []
    for line in open(obs_path):
        line = line.strip()
        if line:
            try:
                obs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return obs


def should_skip(resource, existing_obs, min_interval_hours):
    """Check if resource was observed within min_interval_hours."""
    spider = resource["spider"]
    url = resource["original_url"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_interval_hours)
    for obs in existing_obs:
        if obs.get("spider") == spider and obs.get("resource_url") == redact_url(url):
            ts = obs.get("timestamp", "")
            try:
                obs_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if obs_time > cutoff:
                    return True
            except (ValueError, TypeError):
                continue
    return False


def is_blocked(resource, existing_obs):
    """Check if resource was blocked in previous observations."""
    spider = resource["spider"]
    url = resource["original_url"]
    for obs in existing_obs:
        if obs.get("spider") == spider and obs.get("resource_url") == redact_url(url):
            status = obs.get("status_code")
            if status in (401, 403, 404, 410):
                return True
            if obs.get("error"):
                return True
    return False


def probe_resource(resource, timeout=15):
    """Make a single observation of a resource."""
    spider = resource["spider"]
    url = resource["original_url"]
    method = resource.get("http_method", "GET")
    if not method:
        method = "GET"

    obs = {
        "observation_id": str(uuid.uuid4()),
        "spider": spider,
        "resource_url": redact_url(url),
        "timestamp": now_iso(),
        "http_method": method,
        "status_code": None,
        "etag": None,
        "last_modified": None,
        "cache_control": None,
        "content_type": None,
        "response_body_hash": None,
        "bytes_transferred": 0,
        "elapsed_ms": 0,
        "error": None,
        "suitable_for_comparison": False,
        "normalized_fingerprint": None,
    }

    t0 = time.time()
    try:
        headers = {"User-Agent": USER_AGENT}
        if method == "POST":
            r = requests.post(url, timeout=timeout, headers=headers, allow_redirects=True)
        else:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)

        obs["elapsed_ms"] = int((time.time() - t0) * 1000)
        obs["status_code"] = r.status_code
        obs["etag"] = r.headers.get("ETag")
        obs["last_modified"] = r.headers.get("Last-Modified")
        obs["cache_control"] = r.headers.get("Cache-Control")
        obs["content_type"] = r.headers.get("Content-Type")
        obs["bytes_transferred"] = len(r.content) if r.content else 0
        if r.content:
            obs["response_body_hash"] = hashlib.sha256(r.content).hexdigest()

        # Suitable for comparison: no error, 2xx/3xx status
        if isinstance(r.status_code, int) and 200 <= r.status_code < 400:
            obs["suitable_for_comparison"] = True
    except Exception as e:
        obs["error"] = str(e)
        obs["elapsed_ms"] = int((time.time() - t0) * 1000)

    return obs


def main():
    ap = argparse.ArgumentParser(description="Step 3 Task 3: Live observation of verified resources")
    ap.add_argument("--output", type=str, default="data/seed")
    ap.add_argument("--label", type=str, default="observation")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--min-interval-hours", type=int, default=24)
    ap.add_argument("--skip-blocked", action="store_true", default=True)
    ap.add_argument("--max-resources", type=int, default=0, help="Limit number of resources (0 = all)")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # Load resource inventory
    inventory_path = out / "resource_inventory.csv"
    if not inventory_path.exists():
        print("ERROR: resource_inventory.csv not found. Run evaluate_signals.py first.")
        sys.exit(1)

    resources = list(csv.DictReader(open(inventory_path)))
    # Filter to verified resources only
    verified = [r for r in resources if r.get("contains_location_data") == "verified"]
    print(f"Verified resources: {len(verified)} of {len(resources)}")

    # Load existing observations
    existing_obs = load_observations(out)
    print(f"Existing observations: {len(existing_obs)}")

    # Filter resources
    to_observe = []
    skipped_interval = 0
    skipped_blocked = 0
    for r in verified:
        if args.skip_blocked and is_blocked(r, existing_obs):
            skipped_blocked += 1
            continue
        if should_skip(r, existing_obs, args.min_interval_hours):
            skipped_interval += 1
            continue
        to_observe.append(r)

    if args.max_resources > 0:
        to_observe = to_observe[:args.max_resources]

    print(f"To observe: {len(to_observe)} (skipped: {skipped_interval} interval, {skipped_blocked} blocked)")

    if not to_observe:
        print("No resources to observe. Use --min-interval-hours 0 to force re-observation.")
        sys.exit(0)

    # Observe resources
    obs_path = out / "observations.jsonl"
    observations = []
    done = 0
    with open(obs_path, "a") as f:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futures = {ex.submit(probe_resource, r, args.timeout): r for r in to_observe}
            for fut in as_completed(futures):
                try:
                    obs = fut.result()
                    obs["label"] = args.label
                    observations.append(obs)
                    f.write(json.dumps(obs) + "\n")
                    f.flush()
                except Exception as e:
                    r = futures[fut]
                    print(f"ERROR observing {r['spider']}: {e}")
                done += 1
                if done % 10 == 0 or done == len(to_observe):
                    print(f"Observed {done}/{len(to_observe)}")

    # Summary
    successful = sum(1 for o in observations if o["suitable_for_comparison"])
    with_validators = sum(1 for o in observations if o.get("etag") or o.get("last_modified"))
    errors = sum(1 for o in observations if o.get("error"))
    total_bytes = sum(o.get("bytes_transferred", 0) for o in observations)
    total_elapsed = sum(o.get("elapsed_ms", 0) for o in observations)

    print(f"\n=== Observation Summary (label={args.label}) ===")
    print(f"  Resources observed: {len(observations)}")
    print(f"  Successful (2xx/3xx): {successful}")
    print(f"  With ETag or Last-Modified: {with_validators}")
    print(f"  Errors: {errors}")
    print(f"  Total bytes: {total_bytes}")
    print(f"  Total elapsed: {total_elapsed}ms")
    print(f"  Appended to: {obs_path}")

    # Save summary
    summary = {
        "label": args.label,
        "timestamp": now_iso(),
        "resources_observed": len(observations),
        "successful": successful,
        "with_validators": with_validators,
        "errors": errors,
        "total_bytes": total_bytes,
        "total_elapsed_ms": total_elapsed,
    }
    summary_path = out / "observation_summaries.jsonl"
    with open(summary_path, "a") as f:
        f.write(json.dumps(summary) + "\n")

    print("\nDone. Run again later with a different --label to build a time series.")


if __name__ == "__main__":
    main()
