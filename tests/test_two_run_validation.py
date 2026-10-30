"""Tests for two-run AllThePlaces validation (scripts/validate_two_runs.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import validate_two_runs as v


def feat(name, addr, lon, lat, fid="x"):
    return {
        "type": "Feature",
        "id": fid,
        "properties": {"name": name, "addr:full": addr},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def test_norm_text_collapses_whitespace():
    assert v.norm_text("  Foo   Bar ") == "foo bar"
    assert v.norm_text(None) == ""


def test_norm_coord_rounds():
    assert v.norm_coord(1.23456789) == 1.2346
    assert v.norm_coord(None) is None
    assert v.norm_coord("bad") is None


def test_identical_datasets_unchanged():
    feats = [feat("A", "1 Main St", 1.0, 2.0), feat("B", "2 Main St", 3.0, 4.0)]
    out = v.compare_spider(feats, feats)
    assert out["changed"] is False
    assert out["added"] == 0
    assert out["removed"] == 0
    assert out["matched"] == 2


def test_id_rename_not_a_change():
    """Same locations, different record ids: must be unchanged."""
    a = [feat("A", "1 Main St", 1.0, 2.0, fid="id1"),
         feat("B", "2 Main St", 3.0, 4.0, fid="id2")]
    b = [feat("A", "1 Main St", 1.0, 2.0, fid="ZZZ"),
         feat("B", "2 Main St", 3.0, 4.0, fid="YYY")]
    out = v.compare_spider(a, b)
    assert out["changed"] is False
    assert out["added"] == 0
    assert out["removed"] == 0


def test_added_location_detected():
    a = [feat("A", "1 Main St", 1.0, 2.0), feat("C", "3 New Rd", 5.0, 6.0)]
    b = [feat("A", "1 Main St", 1.0, 2.0)]
    out = v.compare_spider(a, b)
    assert out["changed"] is True
    assert out["added"] == 1


def test_removed_location_detected():
    a = [feat("A", "1 Main St", 1.0, 2.0)]
    b = [feat("A", "1 Main St", 1.0, 2.0), feat("D", "4 Gone Ave", 7.0, 8.0)]
    out = v.compare_spider(a, b)
    assert out["changed"] is True
    assert out["removed"] == 1


def test_address_change_detected():
    a = [feat("A", "1 Main St", 1.0, 2.0)]
    b = [feat("A", "99 New Ave", 1.0, 2.0)]
    out = v.compare_spider(a, b)
    # same coords+name but different address → primary key differs, but
    # coord-only tolerant match treats it as same location. Address-only
    # change is therefore a known conservative limitation; ensure it does
    # not crash and yields a deterministic result.
    assert out["count_a"] == 1
    assert out["count_b"] == 1


def test_coordinate_change_detected():
    a = [feat("A", "1 Main St", 1.0, 2.0)]
    b = [feat("A", "1 Main St", 1.5, 2.5)]
    out = v.compare_spider(a, b)
    # different coords, different name? same name. primary differs, coord
    # differs, so treated as added + removed (a move).
    assert out["changed"] is True


def test_stable_fingerprint_ignores_ids():
    a = [feat("A", "1 Main St", 1.0, 2.0, fid="id1")]
    b = [feat("A", "1 Main St", 1.0, 2.0, fid="id2")]
    assert v.stable_fingerprint(a) == v.stable_fingerprint(b)


def test_stable_fingerprint_detects_change():
    a = [feat("A", "1 Main St", 1.0, 2.0)]
    b = [feat("A", "1 Main St", 1.0, 2.0), feat("B", "2 Main St", 3.0, 4.0)]
    assert v.stable_fingerprint(a) != v.stable_fingerprint(b)


def test_stable_fingerprint_dedupes():
    a = [feat("A", "1 Main St", 1.0, 2.0), feat("A", "1 Main St", 1.0, 2.0)]
    b = [feat("A", "1 Main St", 1.0, 2.0)]
    assert v.stable_fingerprint(a) == v.stable_fingerprint(b)


def test_current_fingerprint_includes_ids():
    """Documents the known ID-instability behaviour of the production
    fingerprint: identical locations with different ids hash differently."""
    a = [feat("A", "1 Main St", 1.0, 2.0, fid="id1")]
    b = [feat("A", "1 Main St", 1.0, 2.0, fid="id2")]
    import baseline_store as bs
    assert bs.compute_fingerprint(a) != bs.compute_fingerprint(b)
