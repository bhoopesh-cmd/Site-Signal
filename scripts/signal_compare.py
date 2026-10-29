#!/usr/bin/env python3
"""Step 3 Task 4: Compare HTTP signals with actual data changes.

Classifies observations into 6-case framework.
Usage:
    python3 scripts/signal_compare.py --output data/seed
"""
import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

USER_AGENT = "ChainXY-seed-collector/1.0 (+research)"


def load_observations(out):
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


def group_observations(obs):
    """Group observations by (spider, resource_url)."""
    groups = {}
    for o in obs:
        key = (o.get("spider", ""), o.get("resource_url", ""))
        groups.setdefault(key, []).append(o)
    # Sort each group by timestamp
    for key in groups:
        groups[key].sort(key=lambda x: x.get("timestamp", ""))
    return groups


def classify_pair(prev, curr):
    """Classify a pair of observations into the 6-case framework."""
    # Validator changed?
    prev_etag = prev.get("etag")
    curr_etag = curr.get("etag")
    prev_lm = prev.get("last_modified")
    curr_lm = curr.get("last_modified")
    validator_changed = (prev_etag != curr_etag) or (prev_lm != curr_lm)

    # Body changed?
    prev_hash = prev.get("response_body_hash")
    curr_hash = curr.get("response_body_hash")
    body_changed = None
    if prev_hash and curr_hash:
        body_changed = (prev_hash != curr_hash)

    # Data changed? (from normalized fingerprint if available)
    prev_fp = prev.get("normalized_fingerprint")
    curr_fp = curr.get("normalized_fingerprint")
    data_changed = None
    if prev_fp and curr_fp:
        data_changed = (prev_fp != curr_fp)

    # HTTP signal
    prev_status = prev.get("status_code")
    curr_status = curr.get("status_code")
    had_304 = (curr_status == 304)
    had_200 = (curr_status == 200)

    # Usable validator?
    has_validator = bool(prev_etag or prev_lm or curr_etag or curr_lm)

    # Request failed?
    prev_failed = prev.get("error") is not None or not (isinstance(prev_status, int) and 200 <= prev_status < 400)
    curr_failed = curr.get("error") is not None or not (isinstance(curr_status, int) and 200 <= curr_status < 400)

    # Classification
    classification = "unknown"
    confidence = "low"
    notes = ""

    if prev_failed or curr_failed:
        classification = "request_failed"
        confidence = "n/a"
        notes = "One or both requests failed; never treat as unchanged"
    elif not has_validator:
        classification = "validator_unavailable"
        confidence = "n/a"
        notes = "No usable ETag or Last-Modified validator"
    elif validator_changed and had_200:
        if data_changed is True:
            classification = "correct_positive"
            confidence = "medium"
            notes = "Validator changed and location data changed"
        elif data_changed is False:
            classification = "false_positive"
            confidence = "medium"
            notes = "Validator changed but location data unchanged (irrelevant change)"
        else:
            classification = "unverified_positive"
            confidence = "low"
            notes = "Validator changed but data change unknown (no fingerprint)"
    elif not validator_changed:
        # Validator unchanged — check if server honored conditional request
        if had_304:
            if data_changed is True:
                classification = "potential_missed_change"
                confidence = "low"
                notes = "Validator unchanged but location data may have changed — investigate"
            elif data_changed is False:
                classification = "correct_negative"
                confidence = "medium"
                notes = "Validator unchanged and location data unchanged"
            else:
                classification = "unverified_negative"
                confidence = "low"
                notes = "Validator unchanged but data change unknown (no fingerprint)"
        elif had_200:
            # Server returned 200 despite unchanged validator — ignored condition?
            if body_changed is False:
                classification = "ignored_condition"
                confidence = "medium"
                notes = "Server returned 200 with identical body despite unchanged validator — conditional header ignored"
            elif data_changed is False:
                classification = "ignored_condition"
                confidence = "medium"
                notes = "Server returned 200 despite unchanged validator — conditional header ignored"
            else:
                classification = "unverified_negative"
                confidence = "low"
                notes = "Validator unchanged, server returned 200"
        else:
            classification = "inconclusive"
            confidence = "low"
            notes = f"Unexpected combination: validator_changed={validator_changed}, status={curr_status}"

    # Weak ETag note
    etag = curr.get("etag") or prev.get("etag") or ""
    if etag.startswith('W/"'):
        notes += " [weak ETag]"

    # Body hash comparison note
    if body_changed is True and not validator_changed:
        notes += " [body changed without validator change]"

    return {
        "validator_changed": validator_changed,
        "body_changed": body_changed if body_changed is not None else "unknown",
        "data_changed": data_changed if data_changed is not None else "unknown",
        "classification": classification,
        "confidence": confidence,
        "notes": notes.strip(),
    }


def main():
    ap = argparse.ArgumentParser(description="Step 3 Task 4: Compare HTTP signals with data changes")
    ap.add_argument("--output", type=str, default="data/seed")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    observations = load_observations(out)
    if not observations:
        print("No observations found. Run live_observe.py first.")
        # Write empty signal_comparison.csv with header
        write_empty(out)
        return

    groups = group_observations(observations)

    pairs = []
    pair_id = 0
    for (spider, url), obs_list in groups.items():
        if len(obs_list) < 2:
            continue
        for i in range(len(obs_list) - 1):
            prev = obs_list[i]
            curr = obs_list[i + 1]
            pair_id += 1
            result = classify_pair(prev, curr)
            pairs.append({
                "observation_pair_id": f"pair_{pair_id:04d}",
                "spider": spider,
                "resource_url": url,
                "prev_observation_id": prev.get("observation_id", ""),
                "curr_observation_id": curr.get("observation_id", ""),
                "prev_timestamp": prev.get("timestamp", ""),
                "curr_timestamp": curr.get("timestamp", ""),
                "prev_status": prev.get("status_code"),
                "curr_status": curr.get("status_code"),
                "prev_etag": prev.get("etag") or "",
                "curr_etag": curr.get("etag") or "",
                "prev_last_modified": prev.get("last_modified") or "",
                "curr_last_modified": curr.get("last_modified") or "",
                "validator_changed": result["validator_changed"],
                "body_changed": result["body_changed"],
                "data_changed": result["data_changed"],
                "classification": result["classification"],
                "confidence": result["confidence"],
                "notes": result["notes"],
            })

    # Write signal_comparison.csv
    out_path = out / "signal_comparison.csv"
    if pairs:
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pairs[0].keys()))
            w.writeheader()
            for p in pairs:
                w.writerow(p)
        print(f"Wrote {len(pairs)} observation pairs to {out_path}")
    else:
        write_empty(out)
        print("No observation pairs (need ≥2 observations per resource). Wrote header only.")

    # Summary
    from collections import Counter
    if pairs:
        print(f"\nClassifications: {Counter(p['classification'] for p in pairs)}")
        print(f"Resources with ≥2 observations: {len(set((p['spider'], p['resource_url']) for p in pairs))}")

    # Pending resources
    pending = [(spider, url) for (spider, url), obs in groups.items() if len(obs) < 2]
    if pending:
        print(f"\nPending resources (only 1 observation): {len(pending)}")
        print("  Run live_observe.py again later to build time series.")


def write_empty(out):
    out_path = out / "signal_comparison.csv"
    cols = ["observation_pair_id", "spider", "resource_url", "prev_observation_id",
            "curr_observation_id", "prev_timestamp", "curr_timestamp", "prev_status",
            "curr_status", "prev_etag", "curr_etag", "prev_last_modified",
            "curr_last_modified", "validator_changed", "body_changed", "data_changed",
            "classification", "confidence", "notes"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()


if __name__ == "__main__":
    main()
