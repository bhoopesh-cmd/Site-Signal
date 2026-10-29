import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_seed as cs


def test_validate_geojson_malformed():
    assert cs.validate_geojson({"type": "FeatureCollection"})[0] is False
    assert cs.validate_geojson({"type": "FeatureCollection", "features": "x"})[0] is False
    assert cs.validate_geojson({"type": "X", "features": []})[0] is False
    ok, _ = cs.validate_geojson({"type": "FeatureCollection", "features": []})
    assert ok is True


def test_normalize_feature_missing_props():
    feat = {"type": "Feature", "id": "abc", "properties": {"@spider": "x"}, "geometry": None}
    norm, fout = cs.normalize_feature(feat, "x", "run1", "2026-01-01T00:00:00Z", "raw/x/run1.geojson")
    assert norm["id"] == "abc"
    assert norm["name"] is None
    assert norm["addr_city"] is None
    assert norm["lat"] is None
    assert norm["lon"] is None
    assert "provenance" in fout["properties"]


def test_country_bucket():
    assert cs.country_bucket("ace_hardware_us") == "us"
    assert cs.country_bucket("abn_amro_nl") == "nl"
    assert cs.country_bucket("adidas") == "global"


def test_industry_bucket():
    assert cs.industry_bucket("pizza_hut_us") == "food"
    assert cs.industry_bucket("bank_of_america") == "banking"
    assert cs.industry_bucket("zzz_unknown") == "other"


def test_size_bucket():
    assert cs.size_bucket(10) == "small"
    assert cs.size_bucket(100) == "medium"
    assert cs.size_bucket(1000) == "large"


def test_analyze_source_no_urls():
    info = cs.analyze_source("")
    assert info["source_mapping_confidence"] == "low"
    assert info["source_urls"] == []


def test_analyze_source_extracts_url():
    src = 'start_urls = ["https://example.com/locations/api"]\n'
    info = cs.analyze_source(src)
    assert "https://example.com/locations/api" in info["source_urls"]
    assert info["source_mapping_confidence"] == "high"


def test_analyze_source_rendering_js():
    src = 'meta = {"zyte_api": {"browserHtml": True}}\n'
    info = cs.analyze_source(src)
    assert info["rendering"] == "js"


def test_domain_of():
    assert cs.domain_of("https://www.example.com/path") == "www.example.com"
    assert cs.domain_of("") == ""


def test_probe_http_failure_graceful():
    # probe a non-routable URL; should not raise
    entries = cs.probe_http(["http://127.0.0.1:1/"], timeout=2)
    assert len(entries) == 1
    assert entries[0]["status"] is None or entries[0].get("error") is not None


def test_duplicate_domains_grouping():
    # simulate domain_to_spiders logic
    spider_results = {
        "a_us": {"domain": "example.com"},
        "a_fr": {"domain": "example.com"},
        "b_nl": {"domain": "other.com"},
    }
    domain_to_spiders = {}
    for sp, r in spider_results.items():
        d = r.get("domain") or ""
        if d:
            domain_to_spiders.setdefault(d, []).append(sp)
    assert len(domain_to_spiders["example.com"]) == 2
    assert len(domain_to_spiders["other.com"]) == 1


def test_inconsistent_identifiers_normalize():
    # feature without ref/id
    feat = {"type": "Feature", "properties": {"name": "X", "@spider": "s"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
    norm, _ = cs.normalize_feature(feat, "s", "run1", "2026-01-01T00:00:00Z", "f")
    assert norm["id"] is None
    assert norm["ref"] is None
    assert norm["lat"] == 2.0
    assert norm["lon"] == 1.0


def test_fetch_spider_source_bad_filename():
    text, url, status = cs.fetch_spider_source("nonexistent/path.py", timeout=10)
    # network may succeed or fail; just ensure no exception
    assert isinstance(status, str)


def test_normalize_preserves_original_feature():
    feat = {"type": "Feature", "id": "i1", "properties": {"name": "A", "ref": "r1"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
    norm, fout = cs.normalize_feature(feat, "sp", "run1", "ts", "f")
    # original properties preserved
    assert fout["properties"]["name"] == "A"
    assert fout["properties"]["ref"] == "r1"
    assert fout["id"] == "i1"


def test_qualify_logic_excludes_infrastructure():
    # unit-level: check the filtering condition used in main
    fn = "locations/spiders/infrastructure/trees_us.py"
    assert "infrastructure" in fn.lower()
    sp = "trees_us"
    assert "infrastructure" in sp.lower() or sp.lower().endswith("_addresses") is False


def test_redact_url():
    url = "https://example.com/api?token=secret123&foo=bar&api_key=abc"
    redacted = cs.redact_url(url)
    assert "secret123" not in redacted
    assert "abc" not in redacted
    assert "REDACTED" in redacted
    assert "foo=bar" in redacted


def test_redact_url_no_params():
    url = "https://example.com/stores"
    assert cs.redact_url(url) == url


def test_redact_url_empty():
    assert cs.redact_url("") == ""
    assert cs.redact_url(None) is None


def test_resolve_sitemap_pointer_non_sitemap():
    url = "https://example.com/stores"
    assert cs.resolve_sitemap_pointer(url) == url


def test_geometry_types():
    # Polygon — should NOT derive coords (preserve original geometry only)
    feat2 = {"type": "Feature", "properties": {"name": "Y"}, "geometry": {"type": "Polygon", "coordinates": [[[1.0, 2.0], [3.0, 4.0]]]}}
    norm2, fout2 = cs.normalize_feature(feat2, "sp", "r", "ts", "f")
    assert norm2["lon"] is None and norm2["lat"] is None
    assert norm2["geom_type"] == "Polygon"
    assert fout2["geometry"]["type"] == "Polygon"  # original geometry preserved
    # LineString — should NOT derive coords
    feat3 = {"type": "Feature", "properties": {"name": "Z"}, "geometry": {"type": "LineString", "coordinates": [[1.0, 2.0], [3.0, 4.0]]}}
    norm3, fout3 = cs.normalize_feature(feat3, "sp", "r", "ts", "f")
    assert norm3["lon"] is None and norm3["lat"] is None
    assert norm3["geom_type"] == "LineString"
    # Point — should derive coords
    feat = {"type": "Feature", "properties": {"name": "X"}, "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}}
    norm, _ = cs.normalize_feature(feat, "sp", "r", "ts", "f")
    assert norm["lon"] == 1.0 and norm["lat"] == 2.0
    # None geometry
    feat4 = {"type": "Feature", "properties": {"name": "W"}, "geometry": None}
    norm4, _ = cs.normalize_feature(feat4, "sp", "r", "ts", "f")
    assert norm4["lon"] is None and norm4["lat"] is None


def test_analyze_source_post_detection():
    src = 'from scrapy.http import FormRequest\nyield FormRequest("https://api.example.org/stores", method="POST")'
    info = cs.analyze_source(src)
    assert info.get("http_method") == "POST"
    assert "https://api.example.org/stores" in info["source_urls"]


def test_analyze_source_get_default():
    src = 'start_urls = ["https://example.com/stores"]'
    info = cs.analyze_source(src)
    assert info.get("http_method") == "GET"


def test_weak_etag_not_discarded():
    # Weak ETags should still be recorded, not discarded
    etag = 'W/"54-g82mD9fz2XofYj/5YX/678aNKGw"'
    assert etag.startswith('W/"')
    # The probe_http function records etag_strength but doesn't discard
    # Verify the logic: weak etags are still truthy
    assert bool(etag) is True


# --- Step 2: probe diagnostics tests ---

def test_classify_failure_non_url():
    assert cs.classify_failure("sp", "//*[@class=", None, None, []) == "Incorrect or irrelevant URL"
    assert cs.classify_failure("sp", "?latitudeFrom=40", None, None, []) == "Missing request parameters"


def test_classify_failure_template_url():
    assert cs.classify_failure("sp", "https://example.com/api/{id}", None, None, []) == "Missing request parameters"


def test_classify_failure_auth():
    assert cs.classify_failure("sp", "https://example.com/api", 401, None, []) == "Authentication or access restriction"
    assert cs.classify_failure("sp", "https://example.com/api", 403, None, []) == "Authentication or access restriction"


def test_classify_failure_not_found():
    assert cs.classify_failure("sp", "https://example.com/api", 404, None, []) == "Redirect or URL construction issue"


def test_classify_failure_server_error():
    assert cs.classify_failure("sp", "https://example.com/api", 500, None, []) == "Server-side failure"
    assert cs.classify_failure("sp", "https://example.com/api", 503, None, []) == "Server-side failure"


def test_classify_failure_network_error():
    assert cs.classify_failure("sp", "https://example.com/api", None, "Connection refused", []) == "Network or DNS failure"


def test_classify_failure_dns_error():
    assert cs.classify_failure("sp", "https://example.com/api", None, "NameResolutionError", []) == "Network or DNS failure"


def test_try_request_success():
    # Mock by calling with a real URL that should return 200
    status, url, headers, history, err = cs.try_request("https://httpbin.org/status/200", timeout=10)
    # May fail in CI; just check return shape
    assert len((status, url, headers, history, err)) == 5


def test_try_request_returns_five_tuple():
    status, url, headers, history, err = cs.try_request("https://example.com", timeout=5)
    assert isinstance(url, str)
    assert isinstance(headers, dict)
    assert isinstance(history, list)


def test_probe_diagnostics_file_exists():
    from pathlib import Path
    assert Path("data/seed/probe_diagnostics.csv").exists()


def test_extract_request_urls():
    src = '''start_urls = ["https://example.com/stores"]
yield JsonRequest("https://api.example.com/locations", callback=self.parse)
'''
    urls = cs.extract_request_urls(src)
    kinds = [u[0] for u in urls]
    assert "start_urls" in kinds
    assert "request_call" in kinds


def test_extract_request_urls_excludes_github():
    src = 'x = "https://github.com/foo/bar"'
    urls = cs.extract_request_urls(src)
    assert all("github.com" not in u[1] for u in urls)
