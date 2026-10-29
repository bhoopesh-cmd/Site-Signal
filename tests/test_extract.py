import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import baseline_store as bs
import extract as ex


def test_extract_json_features():
    data = json.dumps({"stores": [{"name": "A", "lat": 1.0, "lon": 2.0}]})
    features, fp, method = ex.extract_json_features(data, "sp")
    assert features is not None
    assert len(features) == 1
    assert fp is not None
    assert method == "json_api"


def test_extract_json_features_geojson():
    data = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "1", "properties": {"name": "X"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
    ]})
    features, fp, method = ex.extract_json_features(data, "sp")
    assert features is not None
    assert len(features) == 1
    assert fp is not None


def test_extract_json_features_malformed():
    features, fp, method = ex.extract_json_features("not json", "sp")
    assert features is None
    assert fp is None
    assert method is None


def test_extract_json_features_empty():
    features, fp, method = ex.extract_json_features("{}", "sp")
    assert features is None


def test_extract_sitemap_features():
    xml = "<urlset><url><loc>https://example.com/store1</loc><lastmod>2026-01-01</lastmod></url></urlset>"
    urls, lastmods, fp = ex.extract_sitemap_features(xml)
    assert urls is not None
    assert len(urls) == 1
    assert fp is not None


def test_extract_sitemap_empty():
    urls, lastmods, fp = ex.extract_sitemap_features("<urlset></urlset>")
    assert urls is None


def test_extract_resource_dry_run():
    resource = {"spider": "sp", "resource_url": "https://example.com/stores", "primary_strategy": "B", "http_method": "GET"}
    result = ex.extract_resource(resource, dry_run=True)
    assert result["status"] == "would_extract"
    assert result["dry_run"] is True


def test_extract_resource_strategy_d():
    resource = {"spider": "sp", "resource_url": "https://example.com/stores", "primary_strategy": "D", "http_method": "GET"}
    result = ex.extract_resource(resource, dry_run=False)
    assert result["status"] == "fallback_needed"
    assert result["extraction_method"] == "browser_needed"


def test_extract_resource_strategy_e():
    resource = {"spider": "sp", "resource_url": "https://example.com/stores", "primary_strategy": "E", "http_method": "GET"}
    result = ex.extract_resource(resource, dry_run=False)
    assert result["status"] == "skipped"


def test_extract_result_structure():
    resource = {"spider": "sp", "resource_url": "https://example.com/stores", "primary_strategy": "D", "http_method": "GET"}
    result = ex.extract_resource(resource, dry_run=False)
    required = ["extraction_id", "spider", "resource_url", "timestamp", "strategy", "status"]
    for key in required:
        assert key in result


def test_compute_fingerprint():
    feat = [{"type": "Feature", "id": "a", "properties": {"name": "X"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    fp = bs.compute_fingerprint(feat)
    assert fp is not None
    assert len(fp) == 64  # SHA-256 hex


def test_save_extraction_results():
    d = tempfile.mkdtemp()
    results = [{"extraction_id": "1", "spider": "sp", "status": "success"}]
    ex.save_extraction_results(d, results)
    path = Path(d) / "extraction_results.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in open(path)]
    assert len(lines) == 1
