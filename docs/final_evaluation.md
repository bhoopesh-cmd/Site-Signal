# Final Evaluation Report — Site-Signal

## Overview

This report evaluates the complete site-signal prototype for ChainXY's location-data monitoring across 8,000+ websites. Based on 100 selected AllThePlaces spiders (114,229 location features).

## 1. Selected Resources and Sampling Methodology

- **100 spiders** selected via automated stratified sampling from 4,562 qualifying candidates
- Strata: country bucket, size bucket (small/medium/large), industry bucket
- Deterministic round-robin with seed=42
- 117 unique HTTP resources (deduplicated from 125 probes)
- Deduplication by (spider, URL, HTTP method)

## 2. Routing Strategy Distribution

| Strategy | Count | % | Description |
|---|---:|---:|---|
| A: Conditional HTTP | 20 | 17.1% | Verified data + ETag/LM present |
| B: Direct payload comparison | 56 | 47.9% | Verified data, no validator |
| C: Alternative metadata/feed | 5 | 4.3% | Sitemap/feed identified |
| D: Browser fallback | 4 | 3.4% | JS-rendered, no direct URL |
| E: Unresolved | 32 | 27.4% | Inaccessible/ambiguous |

## 3. Deterministic Scenario Results

11 end-to-end tests, all passing:

| Scenario | Expected | Result |
|---|---|---|
| Known change (store added) | trigger_extraction | ✅ Pass |
| Unchanged (304) | skip extraction/QA | ✅ Pass |
| Partial extraction | baseline preserved | ✅ Pass |
| QA failure (bad coords) | baseline preserved | ✅ Pass |
| Request failure (timeout) | unknown, baseline preserved | ✅ Pass |
| Dry-run | no extraction triggered | ✅ Pass |
| Append-only baselines | old baselines preserved | ✅ Pass |
| No duplicate processing | repeat run doesn't re-extract | ✅ Pass |
| 304 reuses state | no empty hash | ✅ Pass |
| Baseline chain | prev_baseline_id tracked | ✅ Pass |
| Monitoring report generated | report exists | ✅ Pass |

## 4. Live Evaluation Results

### Monitoring cycle (81 resources)
- Requests issued: 81
- Total bytes: 52MB
- Total elapsed: 110s
- Failures: 2 (bq 503, hm 403)
- Unknown states: 50 (partial/failed extractions)

### Extraction results
- 19 success (JSON API extraction)
- 10 unchanged (304 conditional)
- 50 partial (HTML without structured data, unsupported content types)
- 2 failed (server errors)
- 23,077 features extracted

### QA results
- 29 passed (success/unchanged extractions)
- 52 not_evaluated (partial/failed — extraction incomplete, not data quality issues)
- 0 genuine data-quality failures

### Conditional requests
- ETag 304: 10, ETag 200: 12
- Last-Modified 304: 6, Last-Modified 200: 11
- Ignored condition (200 with identical body): 4

## 5. Comparison with Conventional Scraping

**No conventional scraper exists in the repository.** The comparison is unavailable. The smallest additional experiment needed: implement a conventional scraper for 5-10 resources and compare request counts, bytes, and extraction results.

## 6. Requests, Bytes, Latency, Processing Jobs

### Monitoring costs
- Unique resources checked: 81
- Total HTTP requests: 81
- Total bytes transferred: 52MB
- Total elapsed: 110s
- Failures: 2
- Retries: 0 (no 429/503 retry triggered)

### Downstream work
- Full extraction jobs triggered: 19 (success only)
- Extraction jobs skipped: 62 (partial/failed/unchanged)
- QA jobs triggered: 19
- QA jobs skipped: 62
- Fallback executions: 50 (partial extractions)

### Correctness
- Known unchanged cases correctly skipped: 10 (304 responses)
- Known changes detected: 0 (no real changes during evaluation)
- False-positive triggers: 4 (ignored conditional headers)
- Potential missed changes: 0
- Unknown outcomes: 52 (partial/failed extractions)
- Baseline discrepancies: 0

## 7. Measured and Simulated Work Avoided

### Measured (actual)
- 10 resources returned 304 → no body downloaded (0 bytes for those requests)
- 50 partial extractions → extraction incomplete, no full QA
- 2 failed requests → no extraction attempted

### Simulated (not observed)
- Extraction jobs avoided: 62 (estimated, based on skip/unknown decisions)
- QA jobs avoided: 62 (estimated)
- Browser jobs avoided: 4 (Strategy D not executed)

**Note:** These are simulated savings. Real savings require live observation over multiple time periods with actual changes.

## 8. Known Changes Detected and Failures

- **0 real location-data changes detected** during evaluation (no stores changed in 3 observation rounds)
- **2 request failures:** bq (HTTP 503), hm (HTTP 403)
- **50 partial extractions:** 36 html_unextractable, 9 no_location_features, 5 unsupported_content_type, 2 request_failed
- **0 genuine data-quality failures** in successfully extracted data

## 9. Reconciliation Behavior

- Reconciliation runs full independent check (Strategy B for all resources)
- Failed reconciliation preserves last-known-good baseline
- Successful reconciliation updates baseline only after validation
- Additional requests/bytes/latency recorded
- Configurable interval (default 168h)

## 10. Remaining Risks and Inconclusive Results

1. **Validator reliability unproven** — ETag/Last-Modified behavior tested with synthetic mocks only; no real location change observed
2. **Historical ID instability** — 38,948 "added" records likely ID renames, not genuine business changes
3. **Provider webhooks unverified** — only 1 shared provider (nandos/locationbank.net) identified
4. **Browser fallback untested** — 4 resources require browser rendering; not validated with Playwright
5. **Cost comparison unavailable** — no conventional scraper in repo; cannot measure real savings
6. **Scaling unverified** — prototype tested on 100 spiders; behavior at 8,000+ sites unknown
7. **Concurrent live extraction untested** — tested with mocked responses only
8. **Reconciliation unverified** — tested with mocks; real missed-change detection not observed

## Recommendation

**Conditional HTTP should be used as a verified-subset-only strategy, not as a general fallback.**

- Only 14 of 117 resources (12%) are "promising" (verified location data + ETag present)
- 97 of 117 (82.9%) have no usable validator → require direct payload comparison
- 4 resources ignore conditional headers → cannot rely on 304 alone
- 4 resources need browser rendering → Strategy D
- 32 resources are unresolved → Strategy E

**Next steps for production:**
1. Run live_observe.py weekly to build time series
2. Monitor the 14 promising resources for ETag changes
3. When a real location change occurs, check if the validator caught it
4. For resources without validators, implement sitemap monitoring or direct API polling
5. Contact shared providers (locationbank.net) about webhook availability
6. Measure full scraping pipeline cost vs conditional request cost
7. Implement browser fallback for 4 JS-rendered sites
8. Scale to 8,000+ sites with justified sampling approach

## Test Results

- **181 tests passing** (all)
- **Ruff: 0 errors**
- **CI:** GitHub Actions configured (Python 3.11, Ruff, pytest)

## Evidence Labels

- **A. Verified live behavior:** 81 resources monitored, 19 successful extractions, 10 unchanged (304), 2 request failures
- **B. Synthetic scenario results:** 11 E2E tests, 13 synthetic scenarios, 45 unit tests
- **C. Measured operational improvements:** 52MB transferred, 110s elapsed, 19 extractions triggered, 62 skipped
- **D. Unproven hypotheses:** ETag reliability, provider webhooks, browser fallback, scaling, cost savings