# ChainXY Seed Dataset Collection

Reproducible collection of a seed dataset from AllThePlaces for evaluating
website change-detection and selective scraping.

## Requirements
- Python 3.11+ (anaconda recommended) with `requests`, `pandas`, `pytest`.

## Usage
```bash
# Basic (current seed only)
python3 scripts/collect_seed.py --sample-size 100 --output data/seed

# With historical comparison (downloads ~2.5GB archive)
python3 scripts/collect_seed.py --sample-size 100 --output data/seed --historical
```

## Options
- `--sample-size N` (default 100)
- `--output PATH` (default data/seed)
- `--timeout SEC` (default 30)
- `--concurrency N` (default 4)
- `--seed N` (default 42)
- `--min-features N` (default 5)
- `--historical` attempt historical comparison
- `--freshness-threshold-days N` (default 14)

## Output
See `data/seed/`: manifest.csv, locations.geojson, changes.csv, http_metadata.jsonl, collection_report.md, raw/.

## Tests
```bash
cd /Users/chavanarohith/Desktop/ChainXY && python3 -m pytest tests/ -q
```