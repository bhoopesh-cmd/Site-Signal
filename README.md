# ChainXY Site-Signal

Reproducible seed dataset and evaluation pipeline for ChainXY website change-detection research.
Uses AllThePlaces as source of real websites and reference location data.

## Requirements
- Python 3.11+ (anaconda recommended) with `requests`, `pandas`, `pytest`.

## Step 1: Collect seed dataset
```bash
# Basic (current seed only)
python3 scripts/collect_seed.py --sample-size 100 --output data/seed

# With historical comparison (downloads ~2.5GB archive)
python3 scripts/collect_seed.py --sample-size 100 --output data/seed --historical
```

### Options
- `--sample-size N` (default 100)
- `--output PATH` (default data/seed)
- `--timeout SEC` (default 30)
- `--concurrency N` (default 4)
- `--seed N` (default 42)
- `--min-features N` (default 5)
- `--historical` attempt historical comparison
- `--freshness-threshold-days N` (default 14)

### Output (Step 1)
See `data/seed/`: manifest.csv, locations.geojson, changes.csv, http_metadata.jsonl, collection_report.md, raw/.

## Step 2: Diagnose failed HTTP probes
```bash
python3 scripts/diagnose_probes.py --output data/seed
```
Produces `data/seed/probe_diagnostics.csv` with failure classifications and retry results.

## Step 3: Validate change signals
```bash
python3 scripts/evaluate_signals.py --output data/seed
```

### Options
- `--timeout SEC` (default 30)
- `--concurrency N` (default 4)
- `--historical-run RUN_ID` (default: auto-detect previous run)
- `--skip-conditional` skip conditional HTTP request tests
- `--skip-historical` skip historical comparison

### Output (Step 3)
- `data/seed/resource_inventory.csv` — deduplicated resource inventory
- `data/seed/conditional_probe_results.csv` — ETag/Last-Modified test results
- `data/seed/provider_groups.csv` — shared data provider analysis
- `data/seed/signal_evaluation.csv` — per-resource evaluation metrics
- `data/seed/changes.csv` — historical change candidates

## Tests
```bash
cd /Users/chavanarohith/Desktop/ChainXY && python3 -m pytest tests/ -q
```
45 tests (28 Step 1+2, 17 Step 3). All passing.

## Data files (gitignored)
- `data/seed/raw/` — per-spider GeoJSON files (~116MB)
- `data/seed/locations.geojson` — 114,229 features (111MB, exceeds GitHub 100MB limit)

Regenerate with: `python3 scripts/collect_seed.py --sample-size 100 --output data/seed`