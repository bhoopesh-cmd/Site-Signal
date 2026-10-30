# Site-Signal — Technical README

**Audience:** engineering leadership and engineers evaluating or extending the prototype.

## Summary

Site-Signal is a decision layer that precedes ChainXY's extraction pipeline. For each monitored source it decides **skip**, **extract**, or **unknown/fallback**, using the cheapest reliable signal available. The governing invariant is **safety over savings**: a source is skipped only when a trustworthy signal *and* a matching normalized location fingerprint agree; every uncertain or failed state routes to extraction.

Two tiers exist. **Check-based polling is implemented and validated (retrospectively).** **Event-driven monitoring is proposed and not implemented.**

## Design principles

1. **Never skip on uncertainty.** `unknown` / `failed` → extraction, never a skip.
2. **Compare location data, not bytes or record IDs.** Normalized fingerprints ignore banners, timestamps, and AllThePlaces ID churn.
3. **Append-only baselines.** A baseline is created on 200/validated responses and reused on 304; last-known-good is preserved on failure.
4. **Targeted triggers.** Only changed resources invoke extraction + QA.
5. **Reproducibility.** Public AllThePlaces datasets provide independent ground truth; no self-validation by re-scraping.

## Architecture

### Tier 1 — Event-driven (proposed; not implemented)

Target design:

1. **Ingress:** receive events from authorized integrations (Yext webhooks; location-API change feeds).
2. **Identity mapping:** event → ChainXY brand + location record (provider ID ↔ internal location).
3. **Meaningful-field filter:** diff against the stored record; keep changes to name, address, coordinates, hours, status; drop cosmetic/unrelated updates.
4. **Targeted dispatch:** queue extraction + QA for the affected brand/location only.

Requirements before this is real: provider authorization, event schema contracts, idempotency/dedup (webhooks are at-least-once), and replay handling. None of this exists in the prototype.

### Tier 2 — Check-based polling (implemented)

Per-resource strategy assignment, stored in `data/seed/monitoring_registry.csv`:

| Code | Strategy | Signal |
|---|---|---|
| A | Conditional HTTP | ETag / Last-Modified; conditional request first (no redundant unconditional GET) |
| B | Direct payload | Full fetch → normalized location fingerprint compare |
| C | Metadata/feed | Sitemap/feed change hint |
| D | Browser fallback | Playwright-class rendering (not executed in prototype) |
| E | Unresolved | No viable signal → always extraction |

Registry: **117 resources across 80 spiders** (A=20, B=56, C=5, D=4, E=32). Seed manifest has 100 spiders; 32 have no monitorable registry resource.

### Routing (`scripts/monitor.py::route`)

- **A / 304** → skip (with baseline reuse when available).
- **A / 200** → compare body + fingerprint; changed→extract, changed-body/unchanged-data→skip, unknown→fallback.
- **B** → fingerprint compare; match→skip, differ→extract, inconclusive (no baseline/partial)→unknown→extract.
- **C** → extract if any change signal is true, skip only if all signals are false, else unknown.
- **D** → `fallback_needed`.
- **E** → unknown → extract.

## Normalized fingerprint

Two implementations coexist:

- **Production `baseline_store.compute_fingerprint`** hashes sorted `(id, name, addr, coords)`. **Known defect:** including `id` makes it unstable under AllThePlaces ID regeneration (caused false alarms in validation).
- **Validation `stable_fingerprint`** (`scripts/validate_two_runs.py`) hashes sorted, deduplicated `(name, addr, lat, lon)` with coordinates rounded to 4 dp (~11 m) and **excludes `id`**. This is the intended production behavior and scored 99.35% combined.

Field coverage today is limited (`addr:street_address` / `addr:full`, `name`/`brand`). Expanded address fields, dedup, and a deterministic (non-`hash()`) fallback ID are recommended fixes.

## Resource identity

Matching keys on `(spider, resource_url)`; `normalize_url` is applied only in baseline lookup. `resource_identity` (URL + method + POST body) is defined but unused. **POST bodies are not captured**, and tokenized URLs are requested redacted — both are known gaps that must be fixed before relying on those sources.

## Baseline store (`scripts/baseline_store.py`)

Append-only JSONL: `body_hash`, `normalized_fingerprint`, `etag`, `last_modified`, `status`, `prev_baseline_id`. `is_baseline_eligible(result, has_existing_baseline=...)` prevents 304 overwrites and rejects failed/partial responses. Known defect: stored `feature_count` is `0` (never populated).

## Extraction & QA

- `scripts/extract.py`: JSON API (full feature extraction), sitemap (URLs, **not** features), HTML (mostly unextractable), CSV/custom-XML/JS (partial/stubs).
- `scripts/qa.py`: schema/coordinate/duplicate checks. Known issue: QA is invoked on empty feature lists in some paths, so `feature_count` always fails while status-based overrides report pass.
- `scripts/end_to_end.py`: monitor → selective extract/QA (triggered and unknown) → baseline maintenance.

## Validation methodology

### Historical (retrospective)

Two consecutive AllThePlaces pairs: **10-03→10-10** and **10-10→10-17** (`scripts/validate_two_runs.py`). Ground truth = stable comparison (name+address+coordinate, tolerant of formatting, **no ID reliance**), with two guards:

- **Truncation guard:** >50% record-count swing → `inconclusive` (never a removal).
- **Coverage guard:** live extraction <70% of baseline feature count → `unknown`.

Results (stable fingerprint):

| Cycle | Comparable | Unchanged | Changed | Correct skip | Missed | Correct detection | False alarm | Accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 10-10→10-17 | 90 | 67 | 23 | 67 | 0 | 23 | 0 | 100% |
| 10-03→10-10 | 65 | 43 | 22 | 42 | 0 | 22 | 1 | 98.5% |
| **Combined** | **155** | **110** | **45** | **109** | **0** | **45** | **1** | **99.35%** |

Independent cross-check: a coordinate-set-only comparison agreed 85/85 with the stable fingerprint on cycle 1. The single false alarm was an address field going empty→populated at identical coordinates (conservative).

### Prospective (in progress)

`scripts/prospective_validation.py`:

- **baseline**: freeze Data B (10-17) fingerprints + provenance (`data/seed/prospective/baseline_B.jsonl`).
- **monitor**: capture live signals on 2026-10-30 and **freeze** predictions (`predictions.jsonl`) before Data A is inspected.
- **evaluate**: score frozen predictions against the next run when published.

Frozen state: **90 predictions — 4 changed, 86 unknown, 0 unchanged, 0 failed**; 101 requests, ~45.7 MB, ~195 s. Only JSON-API sources yielded a signal; HTML/JS → `unknown` → extraction. The next AllThePlaces run is not yet published, so prospective accuracy is **not yet computable**.

## Testing, lint, CI

- **201 tests** (2 require network; both pass when run individually): collection, signals, monitor routing, baseline store, end-to-end change flow (11 scenarios), two-run and prospective validation logic.
- **Ruff:** 0 errors.
- **CI:** GitHub Actions (`.github/workflows/ci.yml`) — Python 3.11, Ruff, pytest.

## Repository layout

```
scripts/
  collect_seed.py, diagnose_probes.py, evaluate_signals.py   # seed + signal inventory
  monitor.py, baseline_store.py, extract.py, qa.py           # monitoring + extraction core
  end_to_end.py, run_benchmark.py, full_fetch_control.py     # pipeline + benchmark
  validate_two_runs.py, prospective_validation.py            # validation
  live_observe.py, signal_compare.py, synthetic_fixtures.py  # observation + fixtures
data/seed/            # manifests, registry, baselines, predictions, reports
docs/                 # human + validation reports
tests/                # 201 tests
```

Key data: `monitoring_registry.csv`, `baselines.jsonl`, `two_run_comparison.csv`, `cycle2/`, `prospective/`. Large raw GeoJSON caches (`locations.geojson`, `raw/`, `raw_*`) are gitignored.

## How to run

```bash
pip install -r requirements.txt

# Retrospective validation (downloads Data B)
python3 scripts/validate_two_runs.py --download-b \
  --data-a data/seed/raw --run-a 2026-10-17-13-32-31 --run-b 2026-10-10-13-32-41

# Prospective: baseline → monitor → (evaluate when next run exists)
python3 scripts/prospective_validation.py baseline --run-b 2026-10-17-13-32-31
python3 scripts/prospective_validation.py monitor  --run-b 2026-10-17-13-32-31
# python3 scripts/prospective_validation.py evaluate --run-b 2026-10-17-13-32-31 --run-a <next>

pytest -q && ruff check scripts/ tests/
```

## Known limitations / correctness caveats

- Production `compute_fingerprint` includes unstable IDs (use the stable variant).
- Limited address-field coverage; duplicates not deduplicated; 6-dp coordinate rounding in production.
- POST endpoints probed without body; tokenized URLs requested redacted.
- Reconciliation is partially wired (forces Strategy B but not fully baseline-driven).
- Browser fallback (Strategy D) never executed; sitemap counts URLs not features.
- QA feature-count check always fails; baseline `feature_count` stored as 0.
- No production-readiness, bandwidth-savings, or 8,000-site scalability claims.

## What is implemented vs proposed

| Capability | Status |
|---|---|
| Check-based monitoring (conditional HTTP, fingerprints, metadata) | Implemented, retrospectively validated |
| Live check-based savings | Implemented, validation in progress (unproven) |
| Event-driven monitoring (Yext / webhooks / change feeds) | **Proposed — not implemented** |
