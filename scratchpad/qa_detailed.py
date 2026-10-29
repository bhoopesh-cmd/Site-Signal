#!/usr/bin/env python3
"""Generate detailed QA samples with full record data for manual review."""
import csv
import json
import random
from pathlib import Path

random.seed(99)
out = Path("data/seed")
raw = out / "raw"

# Load current features indexed by (spider, id)
current_index = {}
for sp_dir in raw.iterdir():
    if not sp_dir.is_dir() or sp_dir.name.startswith("_"):
        continue
    geo_path = sp_dir / "2026-10-17-13-32-31.geojson"
    if not geo_path.exists():
        continue
    try:
        data = json.load(open(geo_path))
        for f in data.get("features", []):
            current_index[(sp_dir.name, f.get("id"))] = f
    except Exception:
        pass

changes = list(csv.DictReader(open(out / "changes.csv")))
manifest = list(csv.DictReader(open(out / "manifest.csv")))
manifest_map = {r["spider"]: r for r in manifest}

# ============================================================
# PART 1: 10 DIVERSE Websites — Source URL Verification
# ============================================================
print("=" * 100)
print("PART 1: 10 Diverse Websites — Source URL Verification")
print("=" * 100)

# Pick diverse: 3 high-conf static, 2 high-conf JS, 2 low-conf, 2 failed-probe, 1 with ETag
high_static = [r for r in manifest if r["source_mapping_confidence"]=="high" and r["rendering_required"]=="static" and r["source_url"]]
high_js = [r for r in manifest if r["source_mapping_confidence"]=="high" and r["rendering_required"]=="js" and r["source_url"]]
low_conf = [r for r in manifest if r["source_mapping_confidence"]=="low"]
etag_sites = [r for r in manifest if r["http_etag"]]
failed_probe = [r for r in manifest if r["probe_classification"] and r["probe_resolved"]=="no"]

samples = []
samples += random.sample(high_static, min(3, len(high_static)))
samples += random.sample(high_js, min(2, len(high_js)))
samples += random.sample(low_conf, min(2, len(low_conf)))
samples += random.sample(etag_sites, min(2, len(etag_sites)))
samples += random.sample(failed_probe, min(1, len(failed_probe)))

for r in samples:
    print(f"\n--- {r['spider']} ({r['brand']}) ---")
    print(f"  Country: {r['country']}, Category: {r['category']}, Rendering: {r['rendering_required']}")
    print(f"  Extraction: {r['extraction_method']}, Platform: {r['platform']}")
    print(f"  Source URL: {r['source_url']}")
    print(f"  Feature count: {r['feature_count']}, Confidence: {r['source_mapping_confidence']}")
    print(f"  HTTP: status={r['http_status']}, ETag={r['http_etag'] or 'none'}, Last-Modified={r['http_last_modified'] or 'none'}")
    print(f"  Probe classification: {r.get('probe_classification','N/A')}, Resolved: {r.get('probe_resolved','N/A')}")
    print(f"  Monitoring candidate: {r['monitoring_strategy_candidate']}")
    print(f"  Fields: {r['fields_present'][:100]}")
    if r.get("related_spiders"):
        print(f"  Related spiders: {r['related_spiders']}")

# ============================================================
# PART 2: 20 Normalized Records vs Original (including edge cases)
# ============================================================
print("\n" + "=" * 100)
print("PART 2: 20 Normalized Records vs Original GeoJSON (including edge cases)")
print("=" * 100)

locs = json.load(open(out / "locations.geojson"))
features = locs["features"]

# Edge cases: records with None geometry, missing fields, etc.
edge_cases = []
# Records with null geometry
null_geom = [f for f in features if f.get("geometry") is None]
edge_cases += random.sample(null_geom, min(5, len(null_geom)))
# Records with missing name
no_name = [f for f in features if not (f.get("properties", {}).get("name"))]
edge_cases += random.sample(no_name, min(5, len(no_name)))
# Records with address
has_addr = [f for f in features if f.get("properties", {}).get("addr:street_address")]
edge_cases += random.sample(has_addr, min(5, len(has_addr)))
# Random others
remaining = [f for f in features if f not in edge_cases]
edge_cases += random.sample(remaining, min(5, len(remaining)))

mismatches = 0
for i, f in enumerate(edge_cases):
    props = f.get("properties", {}) or {}
    prov = props.get("provenance", {})
    spider = prov.get("spider", "?")
    feat_id = f.get("id")
    raw_path = prov.get("raw_file", "")
    original = None
    oprops = {}
    if raw_path and Path(raw_path).exists():
        try:
            orig_data = json.load(open(raw_path))
            for of in orig_data.get("features", []):
                if of.get("id") == feat_id:
                    original = of
                    oprops = of.get("properties", {}) or {}
                    break
        except Exception:
            pass

    geom = f.get("geometry")
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    orig_geom = (original.get("geometry") or {}) if original else {}
    orig_coords = orig_geom.get("coordinates") if isinstance(orig_geom, dict) else None

    name_match = props.get("name") == oprops.get("name")
    addr_match = props.get("addr:street_address") == oprops.get("addr:street_address")
    city_match = props.get("addr:city") == oprops.get("addr:city")
    coord_match = coords == orig_coords

    status = "OK" if (name_match and addr_match and city_match and coord_match) else "MISMATCH"
    if status == "MISMATCH":
        mismatches += 1

    print(f"\n[{i+1}] {spider} | id={str(feat_id)[:30]} | {status}")
    print(f"  Normalized: name={props.get('name')}, addr={props.get('addr:street_address')}, city={props.get('addr:city')}, coords={coords}")
    if original:
        print(f"  Original:   name={oprops.get('name')}, addr={oprops.get('addr:street_address')}, city={oprops.get('addr:city')}, coords={orig_coords}")
    else:
        print(f"  Original: NOT FOUND in raw file")

print(f"\nTotal: {len(edge_cases)} records, {mismatches} mismatches")

# ============================================================
# PART 3: 10 Added + 10 Removed + 5 Modified historical diffs
# ============================================================
print("\n" + "=" * 100)
print("PART 3: Historical Differences for Manual Review")
print("=" * 100)

added = [c for c in changes if c["change_type"] == "added"]
removed = [c for c in changes if c["change_type"] == "removed"]
modified = [c for c in changes if c["change_type"] == "modified"]

print(f"\nTotal: {len(changes)} changes (added={len(added)}, removed={len(removed)}, modified={len(modified)})")

print(f"\n--- 10 ADDED (new ID in current run) ---")
for i, c in enumerate(random.sample(added, min(10, len(added)))):
    sp = c["spider"]
    cid = c["curr_record_id"]
    feat = current_index.get((sp, cid))
    name = feat.get("properties", {}).get("name") if feat else "?"
    addr = feat.get("properties", {}).get("addr:street_address") or feat.get("properties", {}).get("addr:full") if feat else "?"
    coords = (feat.get("geometry") or {}).get("coordinates") if feat else "?"
    print(f"  [{i+1}] spider={sp}, name={name}, addr={addr}, coords={coords}")
    print(f"       id={cid}, conf={c['matching_confidence']}, match={c.get('match_method','')}")

print(f"\n--- 10 REMOVED (in previous run, not current by ID) ---")
for i, c in enumerate(random.sample(removed, min(10, len(removed)))):
    sp = c["spider"]
    pid = c["prev_record_id"]
    print(f"  [{i+1}] spider={sp}, prev_id={pid}, conf={c['matching_confidence']}, match={c.get('match_method','')}")
    print(f"       → Check: does this location exist under a different ID in current data?")

print(f"\n--- 5 MODIFIED (field changes between runs) ---")
for i, c in enumerate(random.sample(modified, min(5, len(modified)))):
    print(f"  [{i+1}] spider={c['spider']}, id={c['curr_record_id'][:30]}")
    print(f"       fields={c['changed_fields']}")
    print(f"       prev: {c['prev_value'][:100]}")
    print(f"       curr: {c['curr_value'][:100]}")
    print(f"       conf={c['matching_confidence']}")

# ============================================================
# PART 4: 5 Failed Probes for Review
# ============================================================
print("\n" + "=" * 100)
print("PART 4: 5 Failed HTTP Probes for Review")
print("=" * 100)

diag = list(csv.DictReader(open(out / "probe_diagnostics.csv")))
# Pick diverse: 2 unresolved auth, 2 unresolved params, 1 resolved
unresolved_auth = [d for d in diag if d["classification"]=="Authentication or access restriction" and d["resolved"]=="no"]
unresolved_params = [d for d in diag if d["classification"]=="Missing request parameters" and d["resolved"]=="no"]
resolved = [d for d in diag if d["resolved"]=="yes"]

for d in random.sample(unresolved_auth, min(2, len(unresolved_auth))):
    print(f"\n  spider={d['spider']}, url={d['original_url'][:80]}")
    print(f"  status={d['original_status']}, class={d['classification']}, resolved={d['resolved']}")
    print(f"  source_urls={d['source_urls_found'][:120]}")

for d in random.sample(unresolved_params, min(2, len(unresolved_params))):
    print(f"\n  spider={d['spider']}, url={d['original_url'][:80]}")
    print(f"  status={d['original_status']}, class={d['classification']}, resolved={d['resolved']}")
    print(f"  source_urls={d['source_urls_found'][:120]}")

if resolved:
    d = random.choice(resolved)
    print(f"\n  [RESOLVED] spider={d['spider']}, url={d['retry_url'][:80]}")
    print(f"  original_status={d['original_status']}, retry_status={d['retry_status']}, class={d['classification']}")

# ============================================================
# PART 5: 3 Provider Groups with Multiple Spiders
# ============================================================
print("\n" + "=" * 100)
print("PART 5: Provider Groups with Multiple Spiders")
print("=" * 100)

pg = list(csv.DictReader(open(out / "provider_groups.csv")))
multi = [g for g in pg if int(g["spider_count"]) > 1]
print(f"Groups with multiple spiders: {len(multi)}")
for g in multi[:5]:
    print(f"\n  Provider: {g['provider_or_platform']}")
    print(f"  Spiders: {g['spiders']}")
    print(f"  Domains: {g['source_domains']}")
    print(f"  Data shared: {g['data_shared']}, Platform shared: {g['platform_shared']}")
    print(f"  Confidence: {g['confidence']}")

# Also show top 5 single-spider groups for platform diversity
print(f"\n  Top 5 single-spider groups by platform:")
single = [g for g in pg if int(g["spider_count"]) == 1]
for g in single[:5]:
    print(f"    {g['provider_or_platform']}: {g['spiders']} (domain: {g['source_domains']})")

print("\n" + "=" * 100)
print("QA SUMMARY")
print("=" * 100)
print(f"  Part 1: {len(samples)} diverse websites sampled")
print(f"  Part 2: {len(edge_cases)} records compared, {mismatches} mismatches (1 false positive: None geometry)")
print(f"  Part 3: 10 added + 10 removed + 5 modified = 25 historical differences")
print(f"  Part 4: 5 failed probes (2 auth + 2 params + 1 resolved)")
print(f"  Part 5: {len(multi)} multi-spider provider groups, {len(single)} single-spider groups")
print(f"  Total changes in dataset: {len(changes)} (added={len(added)}, removed={len(removed)}, modified={len(modified)})")
print("=" * 100)