import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import qa


def test_validate_schema_valid():
    features = [{"type": "Feature", "id": "1", "properties": {"name": "Store A", "addr:street_address": "1 Main St"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.validate_schema(features)
    assert result["passed"] is True
    assert result["invalid_count"] == 0


def test_validate_schema_invalid():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": None}]
    result = qa.validate_schema(features)
    assert result["passed"] is False
    assert result["invalid_count"] == 1


def test_validate_schema_empty():
    result = qa.validate_schema([])
    assert result["passed"] is True
    assert result["invalid_count"] == 0


def test_validate_coordinates_valid():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.validate_coordinates(features)
    assert result["passed"] is True
    assert result["invalid_count"] == 0


def test_validate_coordinates_out_of_range():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": {"type": "Point", "coordinates": [200.0, 100.0]}}]
    result = qa.validate_coordinates(features)
    assert result["passed"] is False
    assert result["invalid_count"] == 1


def test_validate_coordinates_null_island():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}}]
    result = qa.validate_coordinates(features)
    assert result["null_island_count"] == 1


def test_validate_feature_count_valid():
    features = [{"type": "Feature", "id": "1", "properties": {}}]
    result = qa.validate_feature_count(features, baseline_count=1)
    assert result["passed"] is True
    assert result["count"] == 1


def test_validate_feature_count_zero():
    result = qa.validate_feature_count([], baseline_count=5)
    assert result["passed"] is False


def test_validate_feature_count_blowup():
    features = [{"type": "Feature", "id": str(i), "properties": {}} for i in range(100)]
    result = qa.validate_feature_count(features, baseline_count=5)
    assert result["passed"] is False


def test_validate_duplicates_none():
    features = [{"type": "Feature", "id": "1", "properties": {"@spider": "sp"}}]
    result = qa.validate_duplicates(features)
    assert result["passed"] is True
    assert result["duplicate_count"] == 0


def test_validate_duplicates_found():
    features = [
        {"type": "Feature", "id": "1", "properties": {"@spider": "sp"}},
        {"type": "Feature", "id": "1", "properties": {"@spider": "sp"}},
    ]
    result = qa.validate_duplicates(features)
    assert result["passed"] is False
    assert result["duplicate_count"] == 1


def test_validate_null_records_none():
    features = [{"type": "Feature", "id": "1", "properties": {"name": "X", "addr:city": "Y"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.validate_null_records(features)
    assert result["passed"] is True
    assert result["null_count"] == 0


def test_validate_null_records_found():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": None}]
    result = qa.validate_null_records(features)
    assert result["passed"] is False
    assert result["null_count"] == 1


def test_run_qa_pass():
    features = [{"type": "Feature", "id": "1", "properties": {"name": "Store A", "addr:street_address": "1 Main St"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.run_qa(features, baseline_count=1)
    assert result["passed"] is True
    assert result["overall_score"] == 1.0
    assert "qa_id" in result
    assert "timestamp" in result


def test_run_qa_fail():
    features = [{"type": "Feature", "id": "1", "properties": {}, "geometry": None}]
    result = qa.run_qa(features, baseline_count=1)
    assert result["passed"] is False
    assert result["overall_score"] < 1.0
    assert len(result["issues"]) > 0


def test_run_qa_empty():
    result = qa.run_qa([], baseline_count=0)
    assert result["passed"] is False  # schema check fails for empty
    assert result["feature_count"] == 0


def test_run_qa_brand_consistency():
    features = [{"type": "Feature", "id": "1", "properties": {"name": "Store A", "brand": "BrandX", "addr:street_address": "1 Main St"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.run_qa(features, baseline_count=1, expected_brand="BrandY")
    assert "brand_consistency" in result["checks"]
    assert result["checks"]["brand_consistency"]["passed"] is False


def test_run_qa_brand_match():
    features = [{"type": "Feature", "id": "1", "properties": {"name": "Store A", "brand": "BrandX", "addr:street_address": "1 Main St"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}]
    result = qa.run_qa(features, baseline_count=1, expected_brand="BrandX")
    assert result["checks"]["brand_consistency"]["passed"] is True


def test_save_qa_results():
    import tempfile
    d = tempfile.mkdtemp()
    results = [{"qa_id": "1", "passed": True, "overall_score": 1.0}]
    qa.save_qa_results(d, results)
    path = Path(d) / "qa_results.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in open(path)]
    assert len(lines) == 1
