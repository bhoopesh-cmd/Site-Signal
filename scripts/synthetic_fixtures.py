#!/usr/bin/env python3
"""Step 3 Task 5: Synthetic test fixtures for change detection logic.

Generates in-memory test data for various change scenarios.
Usage:
    python3 scripts/synthetic_fixtures.py --output data/seed/synthetic
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def make_feature(fid, name, addr, city, coords, phone=None):
    """Create a GeoJSON feature dict."""
    props = {
        "ref": fid,
        "name": name,
        "brand": name.split()[0] if name else None,
        "addr:street_address": addr,
        "addr:city": city,
        "phone": phone,
        "@spider": "synthetic_spider",
    }
    feat = {
        "type": "Feature",
        "id": fid,
        "properties": props,
        "geometry": {"type": "Point", "coordinates": list(coords)} if coords else None,
    }
    return feat


def make_collection(features):
    return {"type": "FeatureCollection", "features": features}


# --- Scenario 1: Store added ---
def scenario_store_added():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [*prev, make_feature("b", "Store B", "2 Main St", "City1", [3.0, 4.0])]
    return prev, curr, "added"


# --- Scenario 2: Store removed ---
def scenario_store_removed():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0]),
            make_feature("b", "Store B", "2 Main St", "City1", [3.0, 4.0])]
    curr = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    return prev, curr, "removed"


# --- Scenario 3: Address changed ---
def scenario_address_changed():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("a", "Store A", "99 New Ave", "City1", [1.0, 2.0])]
    return prev, curr, "modified_address"


# --- Scenario 4: Coordinate changed ---
def scenario_coord_changed():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("a", "Store A", "1 Main St", "City1", [1.5, 2.5])]
    return prev, curr, "modified_coordinates"


# --- Scenario 5: Irrelevant banner change (location data same) ---
def scenario_irrelevant_banner_change():
    # Same location data, different ETag/body hash
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    return prev, curr, "no_change"


# --- Scenario 6: ETag changed, no location change ---
def scenario_etag_changed_no_data_change():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    return prev, curr, "no_change"


# --- Scenario 7: Location changed, validator unchanged ---
def scenario_data_changed_validator_same():
    prev = [make_feature("a", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("a", "Store A", "99 New Ave", "City1", [1.5, 2.5])]
    return prev, curr, "modified"


# --- Scenario 8: Weak ETag ---
def scenario_weak_etag():
    etag = 'W/"abc123"'
    return etag, "weak"


# --- Scenario 9: Server ignores conditional headers ---
def scenario_server_ignores_condition():
    # Server returns 200 with identical body even when If-None-Match is sent
    body = b'{"stores": [{"name": "A"}]}'
    return body, body  # same body, but server returns 200


# --- Scenario 10: Failed request ---
def scenario_failed_request():
    return None, "Connection timed out"


# --- Scenario 11: Inconclusive observation ---
def scenario_inconclusive():
    # Mixed 304/200 across retries
    return [304, 200, 304]


# --- Scenario 12: Duplicate/unstable IDs ---
def scenario_unstable_ids():
    # Same location appears under different IDs
    prev = [make_feature("id_v1", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    curr = [make_feature("id_v2", "Store A", "1 Main St", "City1", [1.0, 2.0])]
    return prev, curr, "renamed"


# --- Scenario 13: Malformed GeoJSON ---
def scenario_malformed_geojson():
    return {"type": "FeatureCollection"}, {"type": "NotAFeatureCollection", "features": "invalid"}


def generate_all(output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    scenarios = {
        "store_added": scenario_store_added(),
        "store_removed": scenario_store_removed(),
        "address_changed": scenario_address_changed(),
        "coord_changed": scenario_coord_changed(),
        "irrelevant_banner": scenario_irrelevant_banner_change(),
        "etag_changed_no_data": scenario_etag_changed_no_data_change(),
        "data_changed_validator_same": scenario_data_changed_validator_same(),
        "unstable_ids": scenario_unstable_ids(),
    }

    for name, (prev, curr, expected) in scenarios.items():
        prev_col = make_collection(prev)
        curr_col = make_collection(curr)
        (out / f"{name}_prev.geojson").write_text(json.dumps(prev_col, indent=2))
        (out / f"{name}_curr.geojson").write_text(json.dumps(curr_col, indent=2))
        (out / f"{name}_expected.txt").write_text(expected)

    # Malformed
    prev_m, curr_m = scenario_malformed_geojson()
    (out / "malformed_prev.geojson").write_text(json.dumps(prev_m))
    (out / "malformed_curr.geojson").write_text(json.dumps(curr_m))

    print(f"Generated {len(scenarios)} scenario pairs + malformed in {out}")


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic test fixtures")
    ap.add_argument("--output", type=str, default="data/seed/synthetic")
    args = ap.parse_args()
    generate_all(args.output)


if __name__ == "__main__":
    main()
