# Signal Evaluation Report — Step 3

## Overview

This report evaluates whether HTTP validators and conditional requests can reduce scraping costs for ChainXY's location-data monitoring across 8,000+ websites. Based on 100 selected AllThePlaces spiders (114,229 location features).

## Methodology

1. **Resource Discovery:** Extracted source URLs from spider source code (regex + analysis). 115 unique resources identified, 117 after deduplication.
2. **Probe Classification:** 79/115 initial probes succeeded (2xx/3xx). 36 failed. 10 resolved after diagnosis, 26 unresolved.
3. **Historical Comparison:** Used HTTP Range requests to extract 91/100 spider snapshots from a previous run (2026-10-10) without downloading the 2.75GB archive. Produced 40,939 candidate changes.
4. **Conditional Request Testing:** Tested ETag (If-None-Match) and Last-Modified (If-Modified-Since) on 117 resources. 3 observations captured over time (baseline, observation2, observation3).
5. **Live Observation:** 20 resources observed 3 times each (append-only, resumable).

## Results

### Q1: How many unique resources were examined?
**117** unique resources (deduplicated from 125 HTTP probe entries across 100 spiders).

### Q2: How many contained relevant location data?
**71** of 117 (60.7%) verified as containing location data. 46 unverified or irrelevant (homepages, sitemaps, robots.txt, Google Maps links, etc.).

### Q3: How many had ETag or Last-Modified?
- **ETag:** 22 resources (18.8%)
- **Last-Modified:** 18 resources (15.4%)
- **Both:** 8 resources
- **Neither:** 97 resources (82.9%)

### Q4: How many successfully returned 304 to conditional requests?
- **ETag (If-None-Match):** 10 of 22 returned 304 (45.5%)
- **Last-Modified (If-Modified-Since):** 6 of 18 returned 304 (33.3%)
- **Combined:** 13 of 30 unique validator resources (43.3%) returned 304 at least once
- **Ignored condition:** 4 resources returned 200 with identical body despite unchanged validator

### Q5: How many historical location changes were identified?
**40,939** candidate changes across 41 spiders:
- 38,948 "added" (likely ID renames — AllThePlaces IDs are unstable between runs)
- 1,802 "removed" (likely ID renames)
- 189 "modified" (genuine field-level changes: opening hours, addresses, coordinates)

Only 189 records (0.5%) matched by stable ID with verifiable field changes.

### Q6: How many changes could be evaluated against an independently captured HTTP signal?
**0** — Historical snapshots do not contain HTTP headers. No historical ETag/Last-Modified data was captured. Cannot correlate past location changes with past HTTP validator behavior.

### Q7: How many false positives, missed changes or inconclusive cases?
- **False positives:** 4 resources returned 200 with identical body (server ignored conditional header) — would trigger unnecessary scraping
- **Missed changes:** 0 observed (no location data changed between observations)
- **Inconclusive:** 18 resources had no usable validator (validator_unavailable classification)
- **Unverified:** 1 (validator changed but data change unknown)

### Q8: How many websites or spiders share identifiable providers?
- **1 multi-spider group:** nandos (bh, om, zw) share `api.locationbank.net/storelocator/StoreLocatorAPI`
- **97 single-spider groups** — each site uses a unique domain/API
- No provider webhooks verified

### Q9: Which resources are promising for direct API monitoring?
**14 resources** marked "promising" (verified location data + ETag present):
- 3 nandos endpoints (JSON API, locationbank.net)
- 2 aland_post_ax pages (HTML with ETag)
- 2 ethiopost_et endpoints
- 2 papa_johns endpoints (cr, qa)
- 1 dominos_pizza_pk (JSON API)
- 1 or_en_cash_fr (HTML with ETag)
- 1 carls_jr_us (robots.txt/sitemap with weak ETag)
- 1 casa_do_pao_de_queijo_br
- 1 my_eye_dr (sitemap with strong ETag)

### Q10: Which questions require a longer live observation period?
1. **Validator reliability:** Need ≥2 weeks of observations to see if ETags change when location data changes
2. **False positive rate:** Need location data to change while validator stays same (or vice versa)
3. **Missed change detection:** Need a real store opening/closing/moving event
4. **Provider webhook availability:** Need to contact providers or check documentation
5. **Cost comparison:** Need to measure full scraping pipeline vs conditional requests

## Signal Comparison Results (3 observations)

| Classification | Count | Description |
|---|---|---|
| validator_unavailable | 18 | No ETag or Last-Modified |
| ignored_condition | 4 | Server returned 200 with identical body |
| request_failed | 1 | Request error |
| unverified_positive | 1 | Validator changed, data change unknown |

No correct_negative or correct_positive cases observed (location data hasn't changed between observations).

## Key Findings

1. **Conditional requests work mechanically** — 13/30 resources returned 304. But this only proves the server honors the condition, not that the validator tracks location data.

2. **Most resources have no usable validator** — 72.6% have neither ETag nor Last-Modified. These require direct payload comparison or sitemap monitoring.

3. **Weak ETags are present but untested** — 4 resources have weak ETags (W/"..."). These can still support If-None-Match but may change without content changes.

4. **Servers sometimes ignore conditional headers** — 4 resources returned 200 with identical body. This would cause unnecessary scraping if relied upon.

5. **Provider sharing is rare** — only 1 of 98 groups has multiple spiders. Most sites use unique data sources.

6. **Historical ID instability** — AllThePlaces IDs change between runs, making 38,948 "added" records unverifiable as genuine business changes.

## Recommendation

**Conditional HTTP should be used as a verified-subset-only strategy, not as a general fallback.**

- **14 of 117 resources** are "promising" (verified location data + ETag present)
- **97 of 117 resources** (82.9%) have no usable validator → require direct payload comparison
- **4 resources** ignore conditional headers → cannot rely on 304 alone
- **Live observation over multiple time periods** is needed to validate ETag/Last-Modified reliability

**Next steps:**
1. Run live_observe.py weekly to build time series
2. Monitor the 14 promising resources for ETag changes
3. When a real location change occurs, check if the validator caught it
4. For resources without validators, implement sitemap monitoring or direct API polling
5. Contact shared providers (locationbank.net) about webhook availability
6. Measure full scraping pipeline cost vs conditional request cost

## Honest Limitations

- Validator reliability cannot be proven from single observations
- Historical HTTP headers were not captured; cannot correlate past changes with past validators
- No real location-data changes observed during the evaluation period
- Synthetic tests validate logic only, not real-world accuracy
- Provider webhooks cannot be claimed without verified access
- 26 of 36 failed probes remain unresolved
- 38,948 "added" records likely ID renames, not genuine business changes
- No monetary cost estimates without known rates