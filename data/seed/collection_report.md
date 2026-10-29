# Collection Report

- Collection timestamp: 2026-10-28T20:50:14.686510+00:00
- Run ID: 2026-10-17-13-32-31
- Run end_time: 2026-10-25T04:28:58Z
- Freshness status: fresh
- History newest run_id: 2026-10-17-13-32-31 (start: 2026-10-17T13:32:31Z)
- Sample size target: 100
- Spiders selected: 100
- Successful current outputs: 100
- Failed/incomplete: 0
- Total reference features: 114229
- HTTP resources probed (initial): 115
- HTTP probes successful (initial, 2xx/3xx): 79
- HTTP probes failed (initial): 36
- Retry probes (Step 2): 10 resolved
- Resources with ETag: 22
- Resources with Last-Modified: 18
- Historical comparison: deferred (use --historical)

## Sampling methodology
Automated stratified sampling over qualifying spiders (errors==0, features>=5, not infrastructure/addresses). Strata: country bucket (from spider name), size bucket (small<50, medium 50-500, large>500), industry bucket (keyword from spider name). Deterministic round-robin selection with seed=42.

## Probe Diagnostics (Step 2)

### Failure classification (36 original failed probes)
| Classification | Count |
|---|---|
| Missing request parameters | 15 |
| Authentication or access restriction | 8 |
| Incorrect or irrelevant URL | 4 |
| Redirect or URL construction issue | 4 |
| Network or DNS failure | 3 |
| Server-side failure | 2 |

### Resolution outcome
- Resolved: 10 (retried with corrected URL or browser headers, verified relevant)
- Unresolved: 26 (auth required, template params, bot protection, DNS, server errors)

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

### Unresolved probes (26)
- Authentication/access restriction (8): an_post_ie API, cancer_research_uk_gb, easybox_ro, guernsey_post_gg, libya_post_ly, mcdonalds_lt, mcdonalds_lv, san_ju_san_bank_jp
- Missing parameters (15): bq API, ici_paris_xl (2), kazpost_kz (2), latvijas_pasts, maltapost_mt, ntt_le_perc_jp, posta_moldovei_md, san_ju_san_bank_jp, cotton_on_group, fare_rata_pf detail, correo_paraguayo_py, camille_la_vie_us (2)
- Redirect/URL construction (4): cotton_on_group, jersey_post_je, nandos_bh (resolved variant), nandos_zw (resolved variant)
- Network/DNS (3): costa_coffee, correo_paraguayo_py, kazpost_kz
- Server-side (2): bq (api.kingfisher.com 503), ntt_le_perc_jp (503)
- Incorrect/irrelevant (4): camille_la_vie_us (XPath captured), howard_county (robots.txt), royal_gibraltar (resolved), correo_paraguayo_py (Google Maps KML)

### False positives corrected
- maisons_du_monde: initially "resolved" (200) but re-verified as 403 (Cloudflare bot protection) → unresolved
- howard_county_public_library: initially "resolved" to robots.txt → reclassified as non-data resource

## Monitoring viability assessment
- **Provider webhooks:** Not yet identified in this sample; needs platform profiling.
- **HTTP conditional requests (ETag/Last-Modified):** 22 resources with ETag, 18 with Last-Modified. Reliability unverified.
- **Direct API endpoints:** 3 resolved (nandos x3) return JSON directly. Several others require auth/params.
- **HTML page monitoring:** 7 resolved probes point to store-locator pages. These are candidate monitoring resources.
- **Browser-dependent:** 6 spiders marked as JS-rendered (zyte_api/browserHtml). Their source URLs may require browser interaction.

## Limitations
- AllThePlaces is reference data, not guaranteed ground truth.
- Source mapping is heuristic (regex + source analysis); confidence reflects evidence.
- HTTP metadata is baseline only; ETag/Last-Modified reliability not asserted.
- Historical comparison deferred by default due to ~2.5GB archive size.
- 26 of 36 failed probes remain unresolved; many require auth/params not available.
- Resolved probes point to alternate resources (HTML pages) in some cases, not the optimal API endpoints.

## Step 3: Signal Evaluation

### Historical changes
- Candidate changes: 40,939 (38,948 added, 1,802 removed, 189 modified)
- Spiders with changes: 41
- Historical spiders found: 91/100
- ID stability: low (most IDs changed between runs)

### Conditional requests
- Resources tested: 117
- ETag 304: 10, ETag 200: 12
- Last-Modified 304: 6, Last-Modified 200: 11
- Ignored condition (200 with identical body): 4
- Validator reliability: unverified (single observation)

### Live observation
- Baseline: 5 resources observed
- Observation 2: 20 resources observed
- Observation 3: 20 resources observed
- Total observations: 25 (append-only)
- Signal comparison pairs: 24

### Provider groups
- Total groups: 98
- Multi-spider groups: 1 (nandos/locationbank.net)
- Webhooks verified: 0

### Monitoring viability
- Promising resources: 14 (verified location data + ETag)
- Possible resources: 57 (verified location data, no ETag)
- Unverified: 46 (no location data or inaccessible)

### Recommendation
Conditional HTTP: verified-subset-only. Live observation needed for validation.