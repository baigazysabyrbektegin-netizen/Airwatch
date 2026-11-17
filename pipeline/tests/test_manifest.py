import json

import pytest

from pipeline import manifest as mf


def test_strip_html_keeps_breaks_and_unescapes():
    text = mf.strip_html("<p>A &amp; B</p><ul><li>one</li><li>two&nbsp;x</li></ul>tail<br/>end")
    assert text.splitlines() == ["A & B", "one", "two x", "tail", "end"]


def test_strip_html_empty():
    assert mf.strip_html(None) == ""


def test_parse_checksum():
    assert mf.parse_checksum("md5:ABCdef") == ("md5", "abcdef")
    for bad in ("", "md5", "md5:", ":abc", "nosuchalgo:abc"):
        with pytest.raises(ValueError):
            mf.parse_checksum(bad)


def test_hash_file_streams(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello" * 1000)
    h = mf.hash_file(p)
    import hashlib

    assert h["md5"] == hashlib.md5(b"hello" * 1000).hexdigest()
    assert h["sha256"] == hashlib.sha256(b"hello" * 1000).hexdigest()


def test_description_excerpt_and_provenance_flags():
    text = "GIS roads: File: roads.rar (shapefiles extracted from openstreetmaps)\nOther line"
    ex = mf.description_excerpt(text, "roads.rar")
    assert ex and "openstreetmaps" in ex
    prov = mf.provenance_for(ex)
    assert prov["third_party_origin_indicated"] is True
    assert prov["review_required"] is True
    assert "openstreetmap" in prov["third_party_markers_found"]


def test_excerpt_includes_following_lines_until_next_file():
    text = "A File: a.pptx summary of\nmonitoring data\ngoogle earth scans\nB File: b.csv model output\nlast"
    ex = mf.description_excerpt(text, "a.pptx")
    assert "google earth scans" in ex and "b.csv" not in ex
    assert mf.provenance_for(ex)["review_required"] is True
    # The nearest earlier non-"File:" line is taken as the group heading (heuristic).
    assert mf.description_excerpt(text, "b.csv").splitlines() == [
        "google earth scans",
        "B File: b.csv model output",
        "last",
    ]
    assert mf.description_excerpt(text, "missing.zip") is None


def test_gridded_population_is_flagged_as_external_input():
    prov = mf.provenance_for("GIS files File: grids.rar (csv file for gridded population 20 years)")
    assert prov["third_party_origin_indicated"] is True
    assert "population" in prov["third_party_markers_found"]


def test_provenance_unknown_when_not_described():
    prov = mf.provenance_for(None)
    assert prov["review_required"] is True
    assert prov["record_text_near_file"] is None
    assert prov["third_party_origin_indicated"] is False


def test_licence_never_invents_file_specific_terms():
    lic = mf.licence_for({"id": "cc-by-4.0"})
    assert lic["record_level_licence_id"] == "cc-by-4.0"
    assert lic["file_specific_licence"] is None
    assert lic["attribution_required"] is True
    none = mf.licence_for(None)
    assert none["record_level_licence_id"] is None
    assert none["record_level_licence_url"] is None
    assert none["attribution_required"] is None


def test_record_section_marks_missing_version():
    rec = {"id": 5, "doi": "d", "metadata": {"title": "T", "description": "<p>x</p>"}}
    sec = mf.build_record_section(rec, record_url="u", api_url="a", retrieved_at="t")
    assert sec["version"] is None
    assert sec["version_note"] == "Not provided by Zenodo."
    assert sec["description_text"] == "x"


def test_write_manifest_atomic_roundtrip_and_no_tmp_left(tmp_path):
    path = tmp_path / "sub" / "m.json"
    mf.write_manifest_atomic(path, {"a": "é"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": "é"}
    assert [p.name for p in path.parent.iterdir()] == ["m.json"]


def test_write_manifest_atomic_failure_keeps_old_file(tmp_path):
    path = tmp_path / "m.json"
    mf.write_manifest_atomic(path, {"ok": 1})
    with pytest.raises(TypeError):
        mf.write_manifest_atomic(path, {"bad": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]
