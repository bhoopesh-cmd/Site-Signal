# ChainXY Site-Signal — Progress Tracker

Last updated: 2026-10-29

---

## Step 1: Automated Seed Dataset Collection — COMPLETE

**Date:** 2026-10-28
**Status:** Done, pushed to GitHub

### What was done
- Built `scripts/collect_seed.py` — automated collection pipeline
- Fetched AllThePlaces latest run metadata (run_id: 2026-10-17-13-32-31, end_time: 2026-10-25)
- Freshness check: fresh (within 14-day threshold, history newest matches latest)
- Fetched stats (_results.json): 5,675 spiders, parsed defensively
- Qualified 4,562 spiders (errors==0, features>=5, not infrastructure/addresses)
- Stratified sampling (country, size, industry) with seed=42 → selected 100
- Downloaded 100 per-spider GeoJSON files (all successful)
- Extracted source URLs from spider Python source code via regex
- Classified rendering: 94 static, 6 JS-heavy (zyte_api/browserHtml)
- HTTP probed 115 representative resources (1-3 per spider, deduplicated)
- Normalized 114,229 features into combined locations.geojson with provenance
- Generated manifest.csv (100 rows × 29 columns), collection_report.md

### Results
| Metric | Value |
|---|---|
| Spiders selected | 100 |
| GeoJSON downloads successful | 100/100 |
| Total reference features | 114,229 |
| HTTP probes | 115 |
| HTTP probes successful (2xx/3xx) | 79 |
| HTTP probes failed | 36 |
| Resources with ETag | 22 |
| Resources with Last-Modified | 18 |
| Source mapping confidence (high) | 80 |
| Source mapping confidence (low) | 20 |
| Failed collections | 0 |

### Files produced
- `data/seed/manifest.csv` (62KB)
- `data/seed/locations.geojson` (111MB, gitignored — regenerate via script)
- `data/seed/http_metadata.jsonl` (74KB, 115 entries)
- `data/seed/collection_report.md`
- `data/seed/changes.csv` (header only — historical deferred)
- `data/seed/raw/` (100 per-spider GeoJSON files, gitignored)
- `README.md`, `.gitignore`

### Tests
- 16 tests (malformed GeoJSON, missing props, failed downloads, duplicate domains, inconsistent IDs)
- All passing

### GitHub
- Repo: https://github.com/bhoopesh-cmd/Site-Signal
- Commit: b255508 (initial) — 13 files pushed
- locations.geojson excluded (111MB > 100MB GitHub limit)

---

## Step 2: Diagnose and Resolve Failed HTTP Probes — COMPLETE

**Date:** 2026-10-28
**Status:** Done, pushed to GitHub

### What was done
- Read all 36 failed probes from http_metadata.jsonl
- Fetched spider source code for all 36 failures
- Classified each failure into 6 categories
- Retried with corrected URLs or browser-like headers
- Verified resolved probes contain location data
- Corrected 2 false positives (maisons_du_monde, howard_county)
- Appended 10 retry results to http_metadata.jsonl (125 total, original 115 preserved)
- Updated manifest.csv with 4 new columns (probe_classification, probe_resolved, monitoring_resource, monitoring_resource_confidence)
- Updated collection_report.md with full diagnostics
- Created probe_diagnostics.csv (36 rows)
- Added 12 new tests (28 total, all passing)

### Classification results (36 probes)
| Classification | Count |
|---|---|
| Missing request parameters | 15 |
| Authentication or access restriction | 8 |
| Incorrect or irrelevant URL | 4 |
| Redirect or URL construction issue | 4 |
| Network or DNS failure | 3 |
| Server-side failure | 2 |

### Resolution results
| Outcome | Count | Details |
|---|---|---|
| Resolved | 10 | blinds_to_go, fare_rata_pf, nandos_bh/om/zw, opt_nc, royal_gibraltar, an_post_ie (HTML alt), bq (HTML alt), hm (HTML alt) |
| Unresolved | 26 | 8 auth required, 15 missing params, 4 redirect/URL, 3 DNS, 2 server errors, 4 irrelevant |

### Resolved probes (monitoring-relevant resources)
1. blinds_to_go — /stores/ HTML page (was 502, resolved with browser headers)
2. fare_rata_pf — /vos-bureaux-de-poste HTML page (embedded office data)
3. nandos_bh — api.locationbank.net JSON API
4. nandos_om — api.locationbank.net JSON API
5. nandos_zw — api.locationbank.net JSON API
6. opt_nc — office.opt.nc agency map page
7. royal_gibraltar_post_office_gi — post.gi pillar box locations page
8. an_post_ie — www.anpost.com/Store-Locator HTML page (API requires subscription key)
9. bq — www.diy.com/find-a-store HTML page (API returned 503)
10. hm — www.hm.com/entrance.ahtml (API requires country param)

### False positives corrected
- maisons_du_monde: initially "resolved" (200) but re-verified as 403 (Cloudflare) → unresolved
- howard_county_public_library: initially "resolved" to robots.txt → reclassified as non-data resource

### Monitoring viability assessment
- Provider webhooks: Not yet identified; needs platform profiling
- HTTP conditional requests (ETag/Last-Modified): 22 ETag, 18 Last-Modified; reliability unverified
- Direct JSON APIs: 3 resolved (nandos x3); several require auth/params
- HTML page monitoring: 7 resolved probes point to store-locator pages
- Browser-dependent: 6 spiders marked JS-rendered

### Files produced/updated
- `data/seed/probe_diagnostics.csv` (22KB, 36 rows)
- `data/seed/http_metadata.jsonl` (74KB, 125 entries)
- `data/seed/manifest.csv` (62KB, 29 columns)
- `data/seed/collection_report.md` (updated)
- `scripts/diagnose_probes.py`
- `tests/test_collect_seed.py` (28 tests, all passing)

### GitHub
- Pushed to https://github.com/bhoopesh-cmd/Site-Signal
- Commit: b255508 → b2c7e1af (merge) → 7d87939 (amend) → b255508 (final push)
- locations.geojson excluded (111MB > 100MB limit)

---

## Step 3: Validate Change Signals and Build Evaluation Dataset — COMPLETE

**Date:** 2026-10-29
**Status:** Done

### What was done
- Created `scripts/evaluate_signals.py` — comprehensive evaluation pipeline
- **Task 1: Resource inventory** — 117 unique resources deduplicated from 125 HTTP probe entries
- **Task 2: Historical comparison** — Used HTTP Range requests to extract 91/100 spider files from previous run zip (2026-10-10) without downloading 2.75GB archive. Produced 40,939 candidate changes.
- **Task 3: Conditional HTTP requests** — Tested ETag (10 returned 304, 12 returned 200) and Last-Modified (6 returned 304, 11 returned 200) on 117 resources
- **Task 4: Provider groups** — 98 groups identified from spider source code (1 group with multiple spiders)
- **Task 5: Cost measurement** — 10,044,270 bytes transferred, 77,570ms total elapsed
- **Task 6: Signal evaluation** — 117 resources evaluated, 71 verified location data, 14 promising for monitoring
- **Task 7: Tests** — 17 new tests (45 total, all passing)

### Results
| Metric | Value |
|---|---|
| Resources inventoried | 117 |
| Verified location data | 71 |
| Resources with ETag | 22 |
| Resources with Last-Modified | 18 |
| Conditional requests (304) | 13 (10 ETag + 6 LM, some overlap) |
| Conditional requests (200) | 19 (12 ETag + 11 LM, some overlap) |
| Historical changes | 40,939 (38,948 added, 1,802 removed, 189 modified) |
| Historical spiders found | 91/100 |
| Provider groups | 98 |
| Promising for monitoring | 14 |
| Total conditional bytes | 10,044,270 |
| Total conditional elapsed | 77,570ms |

### Key findings
- **Conditional requests work mechanically**: 13 resources returned 304 to If-None-Match/If-Modified-Since
- **Validator reliability unverified**: single observation; cannot correlate with location data changes
- **Historical ID instability**: 38,948 "added" records likely due to AllThePlaces ID changes between runs, not genuine business changes
- **Provider sharing rare in sample**: only 1 group with multiple spiders (nandos via locationbank.net API)
- **Direct API monitoring promising**: 3 nandos endpoints return JSON directly; several others require auth/params
- **HTML page monitoring**: 7 resolved probes point to store-locator pages
- **Browser-dependent**: 6 spiders require JavaScript rendering

### Files produced/updated
- `data/seed/resource_inventory.csv` (117 rows)
- `data/seed/conditional_probe_results.csv` (117 rows)
- `data/seed/provider_groups.csv` (98 rows)
- `data/seed/signal_evaluation.csv` (117 rows)
- `data/seed/changes.csv` (40,939 rows)
- `data/seed/collection_report.md` (updated)
- `scripts/evaluate_signals.py`
- `tests/test_step3.py` (17 tests)

### Tests
- 45 total tests (28 original + 17 new)
- All passing

### GitHub
- To be pushed after commit

### Remaining work (from project context immediate priority)
1. Verify discovered source URLs are correct and correspond to location data
2. Obtain historical snapshots or establish live baseline for representative subset
3. Identify verified location changes between snapshots
4. Test whether ETags, Last-Modified or other signals would have detected those changes
5. Compare cost and reliability of conditional requests, direct API checks, normalized-data comparisons and browser extraction
6. Finalize architecture based on measured results

### Architecture layers
1. **Discovery** (done) — find location data sources, download reference, record HTTP baselines
2. **Cheap Signal Checking** (partially done) — test which signals detect changes; assign cheapest per site
3. **Selective Extraction + Targeted QA** (not built) — trigger extraction only on confirmed changes

### Fallback chain per site
Provider webhook → Conditional HTTP → Sitemap/feed check → Direct API fetch → HTML page fetch → Browser scrape → Normalized data comparison

---

## Sitemap Pointer Resolution (robots.txt handling)

- Added `resolve_sitemap_pointer()` to `collect_seed.py` — fetches robots.txt and extracts the `Sitemap:` directive to get the actual sitemap XML URL
- Updated `analyze_source()` to resolve sitemap pointers before recording source URLs
- Updated `evaluate_signals.py` Task 1 to skip robots.txt resources and replace with resolved sitemap URL
- robots.txt is never a location-data resource; it's always a sitemap pointer. Monitoring should target the sitemap XML instead.
- Added `sitemap_crawl` as an extraction method classification for SitemapSpider sites
- Tests: 45 passing

## Key decisions and constraints
- AllThePlaces is reference data, not ground truth
- HTTP validators (ETag/Last-Modified) are candidates, not proven reliable
- Historical comparison deferred (2.5GB archive); use --historical flag
- locations.geojson gitignored (111MB); regenerate via script
- No numerical savings claims until supported by evaluation
- Rate limits, robots.txt, and access restrictions respected throughout

---

## Step 7: End-to-End Evaluation and Final Architecture Assessment — COMPLETE

**Date:** 2026-10-29
**Status:** Done

### What was done
- Created `scripts/end_to_end.py` — orchestrates monitor → extract → QA → baseline update
- Wired QA to real extracted features (stored QA result in extraction result)
- Added fingerprint computation for Strategy B/C in monitor.py
- Made dry-run truly dry (no extraction/QA in dry-run mode)
- Added `docs/final_evaluation.md` — consolidated evaluation report
- Updated `collection_report.md` with Step 7 findings

### Key changes
- Extraction results now include `qa_result` with real validation
- Strategy B/C compute normalized fingerprints from JSON responses
- Dry-run records "would_extract" without fetching
- Baseline updated only on QA-passed extractions
- Append-only logging for all stages

### Results
| Metric | Value |
|---|---|
| Tests | 181 (all passing) |
| Ruff errors | 0 |
| Extractions (live) | 81 (19 success, 10 unchanged, 50 partial, 2 failed) |
| Features extracted | 23,077 |
| QA results | 81 (29 passed, 52 not_evaluated) |
| Baselines | 17 |
| Monitoring runs | 8 |

### Final recommendation
Conditional HTTP: verified-subset-only (14 of 117 resources). Direct payload comparison for 56 resources without validators. Browser fallback for 4 JS-rendered sites. Unresolved for 32 inaccessible/ambiguous sources. Live observation needed to validate validator reliability.

### GitHub
- Pushed to https://github.com/bhoopesh-cmd/Site-Signal

**Date:** 2026-10-29
**Status:** Done

### What was done
- **Task 1: Historical location-change dataset** — 40,939 candidate changes from 91/100 spiders. HTTP Range requests used to extract from previous run zip (no 2.75GB download). match_method column added.
- **Task 2: Conditional-request testing** — 117 resources tested. 13 returned 304 (10 ETag + 6 Last-Modified). 19 returned 200. Validator reliability unverified (single observation).
- **Task 3: Live-observation process** — `scripts/live_observe.py` created. Append-only observations.jsonl. 5 resources observed in smoke test.
- **Task 4: Signal comparison framework** — `scripts/signal_compare.py` created. 6-case classification framework. Reports "pending" with single observation.
- **Task 5: Synthetic tests** — `scripts/synthetic_fixtures.py` + `tests/test_step3_synthetic.py`. 13 scenarios + 13 classification tests.
- **Task 6: Report findings** — Updated PROGRESS.md.

### New files
- `scripts/live_observe.py` — repeatable live observation
- `scripts/signal_compare.py` — 6-case signal comparison
- `scripts/synthetic_fixtures.py` — synthetic test data generator
- `tests/test_step3_synthetic.py` — 26 synthetic tests
- `data/seed/observations.jsonl` — append-only observations
- `data/seed/signal_comparison.csv` — signal comparison results
- `data/seed/synthetic/` — synthetic test fixtures

### Results
| Metric | Value |
|---|---|
| Resources inventoried | 117 |
| Verified location data | 71 |
| Resources with ETag | 22 |
| Resources with Last-Modified | 18 |
| Conditional requests (304) | 13 |
| Conditional requests (200) | 19 |
| Historical changes | 40,939 |
| Historical spiders found | 91/100 |
| Provider groups | 98 |
| Promising for monitoring | 14 |
| Synthetic scenarios | 13 |
| Tests | 76 (all passing) |

### Key findings
- Conditional requests work mechanically (13/30 returned 304)
- Validator reliability unverified (single observation, no historical HTTP headers)
- 38,948 "added" records likely ID renames, not genuine business changes
- Provider sharing rare (1 multi-spider group: nandos)
- Direct API endpoints most promising (3 nandos JSON APIs)
- Live observation process established (append-only, resumable)
- Signal comparison framework ready for multi-observation analysis

### Recommendation
Conditional HTTP should be used as a **verified-subset-only** strategy, not as a general fallback. Only 14 of 117 resources are "promising" (verified location data + ETag present). The remaining 103 resources require direct payload comparison, sitemap monitoring, or browser fallback. Live observation over multiple time periods is needed to validate ETag/Last-Modified reliability before trusting them for production monitoring.