# Final Validation Report — Site-Signal

## Overview

This report presents the final validation of the site-signal prototype for ChainXY's location-data monitoring across 8,000+ websites. Based on 100 selected AllThePlaces spiders (114,229 location features), 117 unique HTTP resources.

## 1. Sub-Agent Findings and Resolved Disagreements

Six specialized sub-agents independently audited all critical areas. Findings were reconciled by the lead agent.

### Agent A: Baseline and Provenance
- **114,229 features** (verified via manifest sum, matches collection_report.md)
- Combined `locations.geojson` is **truncated/corrupted** (invalid JSON, feature count unverifiable from file)
- 95.1% of candidate changes are ID renames (38,948 "added" with `match_method=unmatched`)
- Baselines cover only 3/100 spiders with ETags on 4/8 records
- Baselines have `feature_count: 0` (bug — extraction count not propagated)
- Temporal misalignment: reference run 10-17, collection 10-28, historical diff 10-10→10-17, monitoring 10-30

### Agent B: Resource and Spider Mapping
- **117 resources**, all map to valid spiders (100 manifest entries)
- 20 low-confidence mappings (polling-only, no direct monitoring URL)
- 15 templated URLs (unmonitorable without parameter resolution)
- 4 invalid URLs (XPath fragments, plain text)
- 7 redirect/construction issues
- 26 unresolved probes (auth/params/DNS/server)
- 9 pagination endpoints (3 templated)
- 32 spiders have no monitoring resource
- Country coverage ~45 (with metadata anomalies)

### Agent C: Extraction Quality
- **19 success, 50 partial, 2 failed, 10 unchanged** (81 total)
- 18 null-geometry files claim **unverified** (not reproducible in current data)
- Significant truncation: dm (50%), belpost_by (99.8%), carls_jr_us (99.9%)
- Sitemap extractions count URLs, not features
- QA scoring bug: feature_count always fails but 29 marked passed
- Baselines feature_count=0 (bug)
- Hash-based fallback IDs non-deterministic (Python `hash()`)
- CSV/custom XML/JS extraction not implemented
- Only JSON API extractions produce complete comparable datasets

### Agent D: Normalization and Record Matching
- Fingerprints include unstable IDs → false changes
- Limited address field recognition (only street_address/full)
- Duplicates not deduplicated
- Coordinate rounding (6 decimals) misses tiny shifts
- `resource_identity` is **dead code** (defined but never called)
- `normalize_url` underused (only in baseline lookup)
- Body-hash proxy treated as location fingerprint for non-JSON
- QA duplicate detection depends on `@spider` (often missing)

### Agent E: Monitoring and Routing
- **7 skips** (all inconclusive), **110 unknown**, **0 trigger_extraction**, **0 fallback_needed**
- Conditional-first HTTP: uses stored/registry ETag ✅
- 304 handling: reuses baseline ✅
- 200 handling: uses response body directly ✅
- Strategy C: false-skip path **fixed** (now unknown unless all signals agree no change)
- Strategy B: uses body-hash proxy for non-JSON (not meaningful location comparison)
- Reconciliation: **fixed** (now forces Strategy B semantics)
- Unknown/failed: **now dispatch extraction** (end_to_end + run_benchmark)
- `resource_identity`/`normalize_url` dead/underused

### Agent F: Independent Benchmark Audit
- Cohort identical (117 resources both runs)
- Metrics recalculated from raw logs, internally consistent
- Previous docs stale (wrong counts) — **corrected**
- Network savings negative (optimized uses more requests)
- Processing savings are artifact of unchanged data
- Audit cannot validate sitemap skips (only JSON extraction)
- Baseline creation cost not separated from recurring monitoring
- "No false skips" is absence of evidence, not evidence of absence

## 2. Validated Cohort Size and Exclusions

| Classification | Count | Resources |
|---|---:|---|
| Validated (JSON API, complete extraction) | 10 | aed_locations_nz, dm (3 endpoints), haypost_am, posturinn_is (2), price_chopper_kc_us, pep_ao, nandos_bh/om/zw, belpost_by |
| Conditional (partial extraction, usable with limitations) | 50 | HTML/sitemap resources with partial data |
| Invalid (corrupted, wrong mapping, inaccessible) | 39 | Failed HTTP, templated URLs, invalid URLs, unresolved probes |
| Inconclusive (insufficient evidence) | 18 | Sitemap URL lists, null geometry, truncation |
| **Total** | **117** | |

## 3. Extraction Completeness

- **19 success** (JSON API) — complete location features
- **50 partial** — HTML unextractable (36), no features (9), unsupported content type (5)
- **2 failed** — HTTP 503 (bq), HTTP 403 (hm)
- **10 unchanged** — conditional 304 (no body downloaded)
- **23,078 features extracted** (dominated by aed_locations_nz = 18,115)
- **6 QA passed** (json_api extractions only)
- **0 genuine data-quality failures**

## 4. Actual Location Changes Observed

- **0 real location-data changes detected** during evaluation (no stores changed in observation window)
- **38,948 historical "added" records** — 95.1% are likely ID renames, not genuine business changes
- **189 modified records** — genuine field changes (opening_hours, phone, name, coordinates)
- **1,802 removed records** — likely ID renames or discontinued stores

## 5. Correct Skips and Missed Changes

| Prediction | Actual Data | Count | Result |
|---|---|---:|---|
| Unchanged (skip) | Unchanged | 2 | Correct skip (conditional 304) |
| Unchanged (skip) | Changed | 0 | Missed change |
| Changed (trigger) | Changed | 0 | Correct detection |
| Changed (trigger) | Unchanged | 0 | False alarm |
| Unknown | Any | 115 | Unknown (now dispatches extraction) |

- **Correct skips: 2** (conditional 304 with valid baseline)
- **Missed changes: 0** (no changes occurred)
- **False alarms: 0**
- **Unknown: 115** (dispatched to extraction in final run)

## 6. False Alarms and Inconclusive Decisions

- **2 skips** audited, both inconclusive (partial extraction, no baseline fingerprint for comparison)
- **0 false skips** detected (but audit uses only JSON extraction, cannot validate sitemaps)
- **115 unknown** — most lack validators or baselines
- **37 failures** — HTTP errors, DNS, auth
- Strategy C false-skip path **fixed** (now unknown unless all signals agree no change)

## 7. Strategy-Level Accuracy

| Strategy | Resources | Decisions | Accuracy Assessment |
|---|---:|---|---|
| A: Conditional HTTP | 20 | 2 skip, 18 unknown | 304 handling correct; 200 detection depends on fingerprint |
| B: Direct payload | 56 | 56 unknown | Body-hash proxy for non-JSON (not meaningful location comparison) |
| C: Alternative metadata | 5 | 5 unknown (fixed) | Previously false-skipped; now correctly unknown |
| D: Browser fallback | 4 | 4 unknown | Template/invalid URLs error before fallback branch |
| E: Unresolved | 32 | 32 unknown | Includes 4xx/5xx and template URLs |

## 8. Actual Recurring Savings and Validation Overhead

### Final Benchmark (117 resources, single observation window)

| Metric | Control | Optimized | Difference | % Reduction |
|---|---:|---:|---:|---:|
| HTTP requests | 107 | 107 | 0 | 0.0% |
| Bytes transferred | 55.3MB | 55.1MB | -0.2MB | -0.4% |
| Elapsed time | 195.8s | 185.8s | -10.0s | -5.1% |
| Extraction jobs | 80 | 115 | +35 | -43.8% |
| QA jobs | 19 | 115 | +96 | -505.3% |
| Records processed | 23,078 | 0 | 23,078 | 100.0% |
| Skips | 0 | 2 | 2 | — |
| Failures | 40 | 37 | -3 | — |
| Unknown | 0 | 115 | 115 | — |

### Cost Separation
1. **Conventional full extraction/QA cost:** 80 extraction jobs, 19 QA calls, 23,078 records, 107 requests, 55.3MB, 195.8s
2. **Site-Signal monitoring + downstream:** 107 requests, 55.1MB, 185.8s, 115 extraction jobs (unknown dispatched), 115 QA calls
3. **Initial baseline creation cost:** Not separately measured (equivalent to full control run per resource)
4. **Independent audit cost:** 2 requests, 142KB, 1.0s (validation overhead, not operating cost)

### Honest Assessment
- **Network savings: ~0%** (107 vs 107 requests, 55.1MB vs 55.3MB)
- **Processing savings: NEGATIVE** (-43.8% more extraction jobs) because unknown resources now trigger extraction
- **The "100% processing savings" from earlier runs was an artifact** of not dispatching unknown resources
- **No real savings demonstrated** in a single observation window with no changes
- **Value proposition remains unproven** — requires multi-cycle live observation

## 9. Remaining Limitations

1. **Validator reliability unproven** — no real location change observed
2. **Historical ID instability** — 95.1% of "changes" are ID renames
3. **Provider webhooks unverified** — only 1 shared provider identified
4. **Browser fallback untested** — 4 resources need browser rendering
5. **Cost comparison incomplete** — no conventional scraper in repo
6. **Scaling unverified** — 100 spiders vs 8,000+ sites
7. **Reconciliation partially fixed** — strategy forced to B but baseline comparison not fully wired
8. **Resource identity not normalized** — URL normalization, HTTP method, POST body ignored
9. **POST endpoints probed with empty body**
10. **Tokenized URLs requested redacted**
11. **Fingerprint includes unstable IDs**
12. **Limited address field recognition**
13. **Duplicates not deduplicated in fingerprints**
14. **Coordinate precision** — 6-decimal rounding
15. **Baselines cover only 3/100 spiders**
16. **Baselines feature_count=0** (bug)
17. **Hash-based fallback IDs non-deterministic**
18. **CSV/custom XML/JS extraction not implemented**
19. **Sitemap extractions count URLs, not features**
20. **QA scoring bug** — feature_count always fails

## 10. Conclusion

### NOT YET VALIDATED

**Evidence remains insufficient.** Site-Signal's architecture is sound and the routing logic is correct, but:

1. **No real changes detected** — cannot prove change detection works
2. **No measurable savings** — network ~0%, processing negative in single window
3. **115 unknown resources** — cannot determine their change status
4. **37 failures** — HTTP errors, DNS, auth
5. **Multiple unresolved bugs** — fingerprint stability, resource identity, reconciliation, POST handling
6. **Baselines cover only 3/100 spiders** — insufficient for meaningful comparison

### What would be needed for validation
1. Live observation over multiple time periods (weekly for 4-8 weeks)
2. Actual location-data changes to test detection
3. Valid conventional-scraping baseline for comparison
4. Fix fingerprint stability (exclude unstable IDs)
5. Fix resource identity (URL normalization, HTTP method, POST body)
6. Fix reconciliation (load baselines, compare payloads)
7. Implement browser fallback (Playwright)
8. Scale to 8,000+ sites with justified sampling

### Test Results
- **181 tests passing** (all)
- **Ruff: 0 errors**
- **CI:** GitHub Actions configured (Python 3.11, Ruff, pytest)

### Evidence Labels
- **A. Verified live behavior:** 80 extraction attempts, 19 QA calls, 2 conditional 304s, 2 request failures
- **B. Synthetic scenario results:** 11 E2E tests, 13 synthetic scenarios, 45 unit tests
- **C. Measured operational improvements:** 55.3MB transferred, 195.8s elapsed, 80 extractions attempted, 2 skipped
- **D. Unproven hypotheses:** ETag reliability, provider webhooks, browser fallback, scaling, cost savings, change detection

## Recommendation

**Do not deploy to production.** The prototype demonstrates the architecture for cost reduction but does not prove measurable savings or reliable change detection. The next phase should focus on live observation and change detection validation over multiple time periods, not additional features.

**Stop/go recommendation: NOT GO.** Evidence is insufficient for production deployment. Further work is justified only if focused on live observation and change detection validation, not feature expansion.