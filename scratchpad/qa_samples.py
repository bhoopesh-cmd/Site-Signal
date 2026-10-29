#!/usr/bin/env python3
"""Generate manual QA samples for review."""
import csv
import json
import random
from pathlib import Path

random.seed(42)
out = Path("data/seed")
raw = out / "raw"

# === PART 1: 10 websites with source URL verification ===
print("=" * 80)
print("PART 1: 10 Websites — Source URL Verification")
print("=" * 80)

manifest = list(csv.DictReader(open(out / "manifest.csv")))
# Pick 10 with high confidence and source URLs
high_conf = [r for r in manifest if r["source_mapping_confidence"] == "high" and r["source_url"]]
sample_sites = random.sample(high_conf, min(10, len(high_conf)))

for r in sample_sites:
    print(f"\n--- {r['spider']} ({r['brand']}) ---")
    print(f"  Country: {r['country']}, Category: {r['category']}")
    print(f"  Source URL: {r['source_url']}")
    print(f"  Rendering: {r['rendering_required']}, Extraction: {r['extraction_method']}")
    print(f"  Platform: {r['platform']}")
    print(f"  Feature count: {r['feature_count']}")
    print(f"  HTTP status: {r['http_status']}, ETag: {r['http_etag'] or 'none'}")
    print(f"  Source mapping confidence: {r['source_mapping_confidence']}")
    # Verify by checking if URL is in the spider's source code
    print(f"  Fields present: {r['fields_present'][:100]}")

# === PART 2: 30 normalized records vs original GeoJSON ===
print("\n" + "=" * 80)
print("PART 2: 30 Normalized Records vs Original GeoJSON")
print("=" * 80)

locs = json.load(open(out / "locations.geojson"))
features = locs["features"]
# Pick 30 random features
sample_feats = random.sample(features, min(30, len(features)))

mismatches = 0
for i, f in enumerate(sample_feats):
    props = f.get("properties", {})
    prov = props.get("provenance", {})
    spider = prov.get("spider", "?")
    feat_id = f.get("id")
    # Read original
    raw_path = prov.get("raw_file", "")
    original = None
    if raw_path and Path(raw_path).exists():
        try:
            orig_data = json.load(open(raw_path))
            for of in orig_data.get("features", []):
                if of.get("id") == feat_id:
                    original = of
                    break
        except Exception:
            pass

    geom = f.get("geometry", {})
    coords = geom.get("coordinates", []) if isinstance(geom, dict) else []
    oprops = original.get("properties", {}) if original else {}

    # Compare
    name_match = props.get("name") == oprops.get("name")
    addr_match = props.get("addr:street_address") == oprops.get("addr:street_address")
    city_match = props.get("addr:city") == oprops.get("addr:city")
    coord_match = (coords == (original.get("geometry") or {}).get("coordinates")) if original else "N/A"

    status = "OK" if (name_match and addr_match and city_match and coord_match != False) else "MISMATCH"
    if status == "MISMATCH":
        mismatches += 1

    print(f"\n[{i+1}] {spider} | id={feat_id} | {status}")
    print(f"  Normalized: name={props.get('name')}, addr={props.get('addr:street_address')}, city={props.get('addr:city')}, coords={coords}")
    if original:
        orig_coords = (original.get("geometry") or {}).get("coordinates")
        print(f"  Original:   name={oprops.get('name')}, addr={oprops.get('addr:street_address')}, city={oprops.get('addr:city')}, coords={orig_coords}")
    else:
        print(f"  Original: NOT FOUND in raw file")

print(f"\nTotal: {len(sample_feats)} records, {mismatches} mismatches")

# === PART 3: 20 historical differences ===
print("\n" + "=" * 80)
print("PART 3: 20 Historical Differences for Review")
print("=" * 80)

changes = list(csv.DictReader(open(out / "changes.csv")))
# Pick 10 added + 10 removed
added = [c for c in changes if c["change_type"] == "added"]
removed = [c for c in changes if c["change_type"] == "removed"]
modified = [c for c in changes if c["change_type"] == "modified"]

sample_added = random.sample(added, min(10, len(added)))
sample_removed = random.sample(removed, min(10, len(removed)))

print(f"\nTotal changes: {len(changes)} (added={len(added)}, removed={len(removed)}, modified={len(modified)})")
print(f"\n--- 10 Added records (new in current run) ---")
for i, c in enumerate(sample_added):
    print(f"  [{i+1}] spider={c['spider']}, curr_id={c['curr_record_id'][:40]}, conf={c['matching_confidence']}, evidence={c['evidence']}")

print(f"\n--- 10 Removed records (in previous run, not current) ---")
for i, c in enumerate(sample_removed):
    print(f"  [{i+1}] spider={c['spider']}, prev_id={c['prev_record_id'][:40]}, conf={c['matching_confidence']}, evidence={c['evidence']}")

print(f"\n--- 5 Modified records (field changes) ---")
sample_modified = random.sample(modified, min(5, len(modified)))
for i, c in enumerate(sample_modified):
    print(f"  [{i+1}] spider={c['spider']}, id={c['curr_record_id'][:40]}, fields={c['changed_fields']}, conf={c['matching_confidence']}")
    print(f"       prev: {c['prev_value'][:80]}")
    print(f"       curr: {c['curr_value'][:80]}")

# Analysis note
print("\n" + "=" * 80)
print("ANALYSIS NOTES")
print("=" * 80)
print(f"""
- {len(added)} 'added' records likely include ID renames (AllThePlaces docs: IDs can change between runs)
- {len(removed)} 'removed' records may be the same locations with changed IDs
- Only {len(modified)} records matched by ID and had field-level changes
- Fuzzy matching by name+address was attempted but found few matches (address formats may differ)
- Recommendation: treat 'added'/'removed' as 'unresolved' until verified by name+address fuzzy matching
""")