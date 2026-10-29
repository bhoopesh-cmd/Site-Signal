# ChainXY Site-Signal

Reproducible seed dataset and evaluation pipeline for ChainXY website change-detection research.
Uses AllThePlaces as source of real websites and reference location data.

## Requirements
- Python 3.11+ (anaconda recommended) with `requests`, `pandas`, `pytest`.

## Step 1: Collect seed dataset
```bash
python3 scripts/collect_seed.py --sample-size 100 --output data/seed
```

## Step 2: Diagnose failed HTTP probes
```bash
python3 scripts/diagnose_probes.py --output data/seed
```

## Step 3: Validate change signals
```bash
python3 scripts/evaluate_signals.py --output data/seed
```

## Step 4: Monitoring engine
```bash
# Build registry
python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --dry-run

# Run monitoring cycle
python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --live

# ETag audit
python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --audit-etags

# Reconciliation
python3 scripts/monitor.py --registry data/seed/monitoring_registry.csv --reconcile --interval-hours 168
```

## Step 6: Selective extraction
```bash
# Dry run
python3 scripts/extract.py --output data/seed --dry-run

# Live extraction
python3 scripts/extract.py --output data/seed --live
```

## Step 7: End-to-end evaluation
```bash
# Dry run
python3 scripts/end_to_end.py --output data/seed --dry-run

# Live
python3 scripts/end_to_end.py --output data/seed --live
```

## Live observation
```bash
python3 scripts/live_observe.py --output data/seed --label "baseline"
python3 scripts/signal_compare.py --output data/seed
```

## Tests
```bash
python3 -m pytest tests/ -q
```
181 tests (all passing). Ruff clean.

## Output files
See `data/seed/`: manifest.csv, locations.geojson (gitignored), changes.csv, http_metadata.jsonl, probe_diagnostics.csv, resource_inventory.csv, conditional_probe_results.csv, provider_groups.csv, signal_evaluation.csv, signal_evaluation_report.md, collection_report.md, monitoring_registry.csv, monitor_run.jsonl, monitoring_report.md, baselines.jsonl, extraction_results.jsonl, qa_results.jsonl, observations.jsonl, e2e_run.jsonl, synthetic/

## Data files (gitignored)
- `data/seed/raw/` — per-spider GeoJSON files (~116MB)
- `data/seed/locations.geojson` — 114,229 features (111MB, exceeds GitHub 100MB limit)

Regenerate with: `python3 scripts/collect_seed.py --sample-size 100 --output data/seed`