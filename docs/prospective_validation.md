# Final Prospective Validation — Site-Signal

## Conclusion: PARTIALLY VALIDATED

Site-Signal's location-comparison logic correctly predicts unchanged locations on
real consecutive AllThePlaces datasets (100% and 98.5% on two independent weekly
cycles). However, **the synchronized live monitoring cycle could not produce a
location signal for most sources** (86 of 90 resources were `unknown`), and the
next AllThePlaces run needed to score the frozen prospective predictions is not yet
published. So correct skips are demonstrated on real data, but recurring savings
are not yet realized in live operation and prospective detection accuracy remains
pending one data cycle.

This report closes the research with the evidence available as of **2026-10-30**.

---

## 1. Experiment design

| Item | Value |
|---|---|
| Synchronized baseline (Data B) | AllThePlaces run `2026-10-17-13-32-31` (end `2026-10-25T04:28:58Z`) |
| Eligible cohort | 90 spiders validated as comparable in the retrospective cycle |
| Live monitoring capture | `2026-10-30` (frozen predictions) |
| Next dataset (Data A) | **not yet published** (`latest.json` still returns `2026-10-17`) |
| Ground truth source | AllThePlaces published per-spider GeoJSON only (no re-scraping) |

Pipeline (`scripts/prospective_validation.py`), three phases:

1. **baseline** — froze Data B location fingerprints + provenance for 90 spiders
   into `data/seed/prospective/baseline_B.jsonl` (immutable, `baseline_frozen_at`).
2. **monitor** — captured live Site-Signal signals on 2026-10-30 for every
   monitorable resource of the cohort and **froze predictions** into
   `data/seed/prospective/predictions.jsonl` before Data A was inspected.
3. **evaluate** — scores frozen predictions against Data A vs Data B; **pending**
   until Data A exists.

A prediction is `unchanged` only when the live extracted fingerprint exactly
matches the baseline and coverage is ≥70% of the baseline feature count. Missing
features → `unknown`/`failed`, never a skip.

## 2. Frozen live predictions (2026-10-30)

| Decision | Count |
|---|---:|
| unchanged (safe skip) | 0 |
| changed (trigger extraction) | 4 |
| unknown (fallback required) | 86 |
| failed | 0 |
| **Total** | **90** |

- Monitoring cost: **101 HTTP requests**, **45.7 MB** downloaded, **194.9 s** elapsed.
- `changed` resources: `aed_locations_nz` (18,096→18,115), `haypost_am` (777→778),
  `pep_ao` (64→65), `price_chopper_kc_us` (49→49, fingerprint differs).
- `unknown` reasons: 81 `no_live_location_data` (HTML/JS sources — no extractable
  location data), 5 `incomplete_coverage` (registry endpoint covers only part of
  the spider, e.g. `dm`, `nandos_*`, `belpost_by`).

### Why so many unknown
Live monitoring must fetch and parse the *source* to compute a location
fingerprint. Only 4 of the 90 cohort sources expose complete, extractable location
data live; the rest are HTML/JS pages whose location data cannot be extracted
without the AllThePlaces spider or a browser. The coverage guard correctly
prevented 5 partial endpoints from being mislabelled as changes.

## 3. Algorithm accuracy on real consecutive runs (retrospective)

Two independent weekly cycles were completed with the same stable-fingerprint
methodology (IDs excluded, deduplicated, coordinate-tolerant, truncation guard):

| Cycle | Comparable | Unchanged | Changed | Inconclusive | Correct skip | Missed change | Correct detection | False alarm | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10-10 → 10-17 | 90 | 67 | 23 | 10 | 67 | 0 | 23 | 0 | **100.0%** |
| 10-03 → 10-10 | 65 | 43 | 22 | 28 | 42 | 0 | 22 | 1 | **98.5%** |

- **Missed changes: 0 across both cycles (45 real changes detected, 0 missed).**
- **Skip precision: 100% in both cycles (109 correct skips, 0 false skips).**
- The single cycle-2 "false alarm" (`dominos_pizza_kh`) was an address field that
  went from empty to populated at identical name+coordinates — a conservative
  catch (safe direction), not a missed change.
- The current production fingerprint (which includes record IDs) scored 96.7% and
  92.3% on the same cycles; its only errors were false alarms from AllThePlaces ID
  regeneration, confirming the ID-exclusion fix.

An independent coordinate-set-only comparison agreed with the stable fingerprint
on 85/85 spiders (cycle 1), so the result is not an artifact of the matching code.

## 4. Accuracy against frozen predictions

Prospective scoring (`prediction` vs Data A/Data B) **cannot be computed yet**
because Data A (the run after 2026-10-17) is not published. No accuracy figure is
claimed for the prospective cycle. The frozen predictions are immutable and ready
to be scored by:

```bash
python3 scripts/prospective_validation.py evaluate \
  --run-b 2026-10-17-13-32-31 --run-a <next-run-id> \
  --data-a-dir data/seed/raw   # after downloading the next run
```

Expected scoring given current predictions: at most 4 `correct_detection` /
`false_alarm` outcomes; 86 resources are `fallback_required` by definition.

## 5. Efficiency

| Metric | Value | Basis |
|---|---:|---|
| Retrospective processing savings (potential) | **65.7%** (67/102 resources; 67 extraction + 67 QA jobs) | Cycle 1, stable fingerprint |
| Live prospective safe skips realized | **0** | 0 `unchanged` predictions on 2026-10-30 |
| Live monitoring requests / bytes | 101 / 45.7 MB | measured |
| Bandwidth savings | **0%** | full payload fetched for every resource |
| Baseline creation cost (one-time) | separate; 0 extra fetches (reused published AllThePlaces data) | — |
| Validation overhead (AllThePlaces downloads) | separate; not part of operating cost | — |

**Interpretation:** the algorithm *would* avoid ~66% of extraction/QA work when
comparable extracted data is available (proven), but the live pipeline realizes
none of it today because it cannot fingerprint HTML sources — those fall back to
extraction. The blocker is signal availability, not the comparison logic.

## 6. Monitoring strategies — what is reliable

| Strategy | Cohort resources | Live decisions | Assessment |
|---|---:|---|---|
| A — conditional HTTP | 15 | 1 changed, 14 unknown | No 304s observed live this cycle; validator reliability still unproven. |
| B — direct payload | 34 | 3 changed, 31 unknown | Reliable *when* the source is JSON-extractable (aed, haypost, pep, price_chopper); otherwise no signal. |
| C — metadata/feed | 3 | 3 unknown | No proved metadata→location relationship. |
| D — browser | 1 | 1 unknown | Not executed. |
| E — unresolved | 18 | 18 unknown | No usable signal. |
| (no registry resource) | 19 | 19 unknown | Seed spiders outside the monitorable registry. |

**Reliable:** direct-payload stable fingerprint on JSON APIs (Strategy B). It is
the only strategy that produced definitive live decisions this cycle.
**Not reliable / unproven:** conditional HTTP (no 304 evidence this cycle),
metadata, browser, unresolved.

## 7. What remains unproven

1. **Prospective detection accuracy** — requires Data A (next weekly run), not yet published.
2. **Recurring live savings** — blocked by signal availability: 86/90 sources need extraction anyway.
3. **Conditional-HTTP reliability** — a 304 proves validator equality, not location equality; no live 304 evidence this cycle.
4. **Scale to 8,000+ sites** — validated on 100 spiders / 90 comparable.
5. **HTML/JS/browser coverage** — the dominant reason live skips are not realized.

## 8. Deliverables

- `data/seed/prospective/baseline_B.jsonl` — synchronized baseline (90 spiders, frozen)
- `data/seed/prospective/predictions.jsonl` — frozen predictions (90, 2026-10-30)
- `data/seed/two_run_comparison.csv`, `data/seed/two_run_summary.json` — cycle 1
- `data/seed/cycle2/two_run_comparison.csv`, `two_run_summary.json` — cycle 2
- `scripts/prospective_validation.py`, `scripts/validate_two_runs.py`
- `tests/test_prospective_validation.py` (8), `tests/test_two_run_validation.py` (12)

## 9. Final statement

**Partially validated.** On two real consecutive-run cycles, Site-Signal's
stable-fingerprint comparison correctly skips unchanged locations (109/109) and
detects every real change (45/45) — the comparison logic is sound and safe. In
synchronized live operation, however, it can only classify JSON-API sources (4/90);
the remaining sources produce no location signal and must be extracted, so
recurring savings are not yet realized. One more AllThePlaces cycle is required to
score the frozen prospective predictions. No false skips were observed; no
positive savings are claimed where signals were unavailable.
