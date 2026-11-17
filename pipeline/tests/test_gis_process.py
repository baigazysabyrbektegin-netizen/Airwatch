"""Extraction safety, output writers and an end-to-end run of Stage 4 on synthetic archives."""

import json
import shutil
import subprocess
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

from pipeline import embassy_extract as ex
from pipeline import gis_extract as gx
from pipeline import gis_validate as gv
from pipeline import io_utils as io
from pipeline import manifest as mf
from pipeline import process_gis as pg
from pipeline.tests import gis_helpers as H

pytestmark = pytest.mark.skipif(shutil.which("tar") is None, reason="needs the system tar")


# ------------------------------------------------------------------ extraction safety
def test_role_of_classifies_archive_members():
    assert gx.role_of("a.shp") == "shapefile component" and gx.role_of("x.KML") == "kml layer" and gx.role_of("p.png").startswith("figure")
    assert gx.role_of("bishkek_2018_heat_coal.emi") == "area emissions grid" and gx.role_of("bishkek_2018_brick_coal.ptemi") == "point-source emissions"
    assert "profile" in gx.role_of("camxarea_hourly_profile.csv") and gx.role_of("grids.csv") == "table"


def test_extract_archives_verifies_checksums_and_never_modifies_archives(tmp_path):
    env = H.build_inputs(tmp_path)
    zen = json.loads(env["zpath"].read_text(encoding="utf-8"))
    before = {p: p.read_bytes() for p in env["files"]}
    recs = gx.extract_archives(zen, tmp_path, tmp_path / "out")
    assert [r["archive"] for r in recs] == gx.ARCHIVES and all(f["status"] == "extracted" for r in recs for f in r["files"])
    assert (tmp_path / "out" / "gis_bishkek_roads" / "grids_bishkek_roads.shp").exists()
    assert all(r["sha256"] and r["provenance_review_required"] for r in recs)
    assert {p: p.read_bytes() for p in env["files"]} == before
    again = gx.extract_archives(zen, tmp_path, tmp_path / "out")
    assert {f["status"] for r in again for f in r["files"]} == {"already_extracted_verified"}


def test_extraction_rejects_tampered_archive_and_differing_existing_file(tmp_path):
    env = H.build_inputs(tmp_path)
    zen = json.loads(env["zpath"].read_text(encoding="utf-8"))
    gx.extract_archives(zen, tmp_path, tmp_path / "out")
    (tmp_path / "out" / "gis_bishkek_adm0" / "bishkek_adm2.dbf").write_bytes(b"edited")
    with pytest.raises(ex.ExtractionError, match="refusing to overwrite"):
        gx.extract_archives(zen, tmp_path, tmp_path / "out")
    arc = env["raw"] / "gis_bishkek_roads.rar"
    arc.write_bytes(arc.read_bytes() + b"x")
    with pytest.raises(ex.ExtractionError, match="differs from the Zenodo manifest"):
        gx.extract_archives(zen, tmp_path, tmp_path / "out2")


def test_unsafe_member_names_in_a_gis_archive_are_rejected(monkeypatch, tmp_path):
    listing = "-rw-r--r--  0 0      0      10 Jul 05  2024 ../escape.shp\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=listing, stderr=""))
    with pytest.raises(ex.ExtractionError, match="Unsafe"):
        ex.list_members(tmp_path / "x.rar")


def test_manifest_entry_requires_a_verified_download():
    zen = {"files": [{"filename": "a.rar", "download_status": "failed"}]}
    with pytest.raises(ex.ExtractionError, match="not a verified download"):
        gx.manifest_entry(zen, "a.rar")
    with pytest.raises(ex.ExtractionError):
        gx.manifest_entry(zen, "missing.rar")


# ------------------------------------------------------------------ writers
def test_geoparquet_round_trip_keeps_crs_geometry_and_metadata(tmp_path):
    g = gpd.GeoDataFrame({"id": [1, 2], "v": [0.5, np.nan]}, geometry=[box(74.4, 42.8, 74.41, 42.81), None], crs=4326)
    p = tmp_path / "g.parquet"
    assert io.write_geoparquet(g, p, {"data_kind": "test", "n": 2}) == "written"
    back = gpd.read_parquet(p)
    assert back.crs.to_epsg() == 4326 and back.geometry.iloc[0].equals(g.geometry.iloc[0]) and back.geometry.iloc[1] is None
    assert back["id"].tolist() == [1, 2] and np.isnan(back["v"].iloc[1])
    md = pq.read_schema(p).metadata
    assert b"geo" in md and json.loads(md[b"airwatch.processing"])["data_kind"] == "test"
    assert io.write_geoparquet(g, p, {"data_kind": "test", "n": 2}) == "unchanged"                      # reproducible bytes
    assert [f.name for f in tmp_path.iterdir()] == ["g.parquet"]


def test_plain_parquet_and_geojson_writers(tmp_path):
    df = pd.DataFrame({"cell_id": [1, 2], "x": [1.5, np.nan]})
    assert io.write_parquet(df, tmp_path / "t.parquet", {"k": "v"}) == "written"
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "t.parquet"), df)
    g = gpd.GeoDataFrame({"name": ["a"]}, geometry=[box(74.4, 42.8, 74.41, 42.81)], crs=4326)
    assert io.write_geojson(g, tmp_path / "g.geojson") == "written" and io.write_geojson(g, tmp_path / "g.geojson") == "unchanged"
    back = gpd.read_file(tmp_path / "g.geojson")
    assert back.crs.to_epsg() == 4326 and back["name"].tolist() == ["a"]


def test_guarded_write_refuses_to_replace_a_different_file(tmp_path):
    p = tmp_path / "x.txt"
    assert io.write_text("a", p) == "written" and io.write_text("a", p) == "unchanged"
    with pytest.raises(io.OutputExistsError, match="allow-overwrite"):
        io.write_text("b", p)
    assert p.read_text() == "a" and io.write_text("b", p, True) == "overwritten_with_approval"


# ------------------------------------------------------------------ end to end
@pytest.fixture
def env(tmp_path):
    e = H.build_inputs(tmp_path)
    return SimpleNamespace(tmp=tmp_path, zpath=e["zpath"], files=e["files"], raw=e["raw"])


def run(env, **kw):
    msgs = []
    code = pg.run(zenodo_manifest=env.zpath, interim_dir=env.tmp / "interim", processed_dir=env.tmp / "processed",
                  manifest_path=env.tmp / "gis_processing.json", spec=H.SPEC, out=msgs.append, **kw)
    return code, msgs


def test_end_to_end_outputs_values_and_manifest(env):
    before = {p: mf.hash_file(p, ("sha256",))["sha256"] for p in env.files}
    zen_before = env.zpath.read_bytes()
    code, _ = run(env)
    assert code == 0
    assert {p: mf.hash_file(p, ("sha256",))["sha256"] for p in env.files} == before and env.zpath.read_bytes() == zen_before   # inputs untouched
    P = env.tmp / "processed"
    expected = ["population_grid.parquet", "roads.parquet", "road_density_grid.parquet", "administrative_boundaries.geojson", "context_layers.geojson",
                "camx_pm25_grid.parquet", "emissions_area_grid.parquet", "emissions_point_sources.parquet", "airwatch_spatial_grid.parquet"]
    assert all((P / f).exists() for f in expected)
    grid = gpd.read_parquet(P / "airwatch_spatial_grid.parquet")
    assert len(grid) == 6 and grid.crs.to_epsg() == 4326 and grid["pop_2019"].sum() == 2130
    assert grid["emis_pm25_total_t_yr"].sum() == 58 and grid["emis_point_sources_pm25_t_yr"].sum() == 40
    assert grid["camx_pm25_annual_mean"].round(2).tolist() == [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
    roads = gpd.read_parquet(P / "roads.parquet")
    assert roads.crs.to_epsg() == 4326 and set(roads["road_group"]) >= {"primary", "tertiary", "other_unlisted"} and "fclass" in roads.columns
    pts = gpd.read_parquet(P / "emissions_point_sources.parquet")
    assert len(pts) == 3 and pts.crs.to_epsg() == 4326
    ctx = gpd.read_file(P / "context_layers.geojson")
    assert len(ctx) == 8 and set(ctx["layer"]) >= {"bte_boilers", "dh_districts"} and ctx["layer_meaning_status"].isin(["documented_in_record", "inferred_from_file_name", "unverified"]).all()
    assert (env.tmp / "interim" / "reports" / "gis_validation_report.md").exists() and (env.tmp / "interim" / "validated" / "gis_validation_checks.json").exists()
    report = (env.tmp / "interim" / "reports" / "gis_validation_report.md").read_text(encoding="utf-8")
    for s in ("## 1. Source datasets", "## 3. Coordinate systems", "assumed_wgs84_undeclared", "NOT traffic", "must NOT be added", "not independent", "NOT Bishkek", "## 14. Known limitations"):
        assert s in report or s.lower() in report.lower(), s

    m = json.loads((env.tmp / "gis_processing.json").read_text(encoding="utf-8"))
    assert m["source"]["zenodo_record_id"] == 1 and len(m["source"]["archives"]) == 6 and all(a["verified_against_zenodo_manifest"] for a in m["source"]["archives"])
    assert m["output_crs"] == "EPSG:4326" and m["crs"]["grids_bishkek.shp"]["status"] == "assumed_wgs84_undeclared" and m["crs"]["grids_bishkek.shp"]["declared_crs"] is None
    assert m["crs"]["bishkek_adm2.shp"]["status"] == "declared_wgs84"
    assert m["spatial_resolution"]["cell_size_deg"] == 0.01 and m["data_vintage"]["emissions"] == "2018" and m["data_vintage"]["population"] == "2000-2019" or "2018-2019" in m["data_vintage"]["population"]
    assert m["data_kinds"]["camx"] == "MODELLED" and m["licence"]["attribution_required"] is True and m["known_limitations"] and m["integration_methodology"]
    assert all(len(o["sha256"]) == 64 and o["size_bytes"] > 0 for o in m["outputs"])
    out = {o["path"].rsplit("/", 1)[-1]: o for o in m["outputs"]}
    assert out["airwatch_spatial_grid.parquet"]["sha256"] == mf.hash_file(P / "airwatch_spatial_grid.parquet", ("sha256",))["sha256"]
    assert {c["column"] for c in m["grid_columns"]} == set(grid.columns) and any(a["filename"] == gx.CAMX_FILE for a in m["source"]["archives"])
    assert len([f for e in m["extraction"] for f in e["files"]]) > 20


def test_end_to_end_is_reproducible_and_never_overwrites_a_changed_output(env):
    run(env)
    code, msgs = run(env)
    assert code == 0 and all("[unchanged]" in x for x in msgs if "[" in x and x.strip().endswith("]") and "manifest" not in x.lower())
    # change an input in a way that changes the outputs: new CAMx values, re-verified against a refreshed manifest entry
    camx = env.raw / gx.CAMX_FILE
    H.write_camx(camx, H.SPEC, negative=True)
    zen = json.loads(env.zpath.read_text(encoding="utf-8"))
    e = next(f for f in zen["files"] if f["filename"] == gx.CAMX_FILE)
    h = mf.hash_file(camx, ("md5", "sha256"))
    e.update(size_bytes=camx.stat().st_size, zenodo_checksum={"algorithm": "md5", "value": h["md5"]}, calculated_checksums=h)
    env.zpath.write_text(json.dumps(zen), encoding="utf-8")
    with pytest.raises(io.OutputExistsError, match="allow-overwrite"):
        run(env)
    code, _ = run(env, allow_overwrite=True)
    assert code == 0


def test_end_to_end_flags_point_sources_that_do_not_reconcile(tmp_path):
    e = H.build_inputs(tmp_path, break_reconciliation=True)
    msgs = []
    pg.run(zenodo_manifest=e["zpath"], interim_dir=tmp_path / "i", processed_dir=tmp_path / "p", manifest_path=tmp_path / "m.json", spec=H.SPEC, out=msgs.append)
    m = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert m["validation_summary"]["point_sources_reconcile"] is False
    assert "False" in (tmp_path / "i" / "reports" / "gis_validation_report.md").read_text(encoding="utf-8")


def test_end_to_end_missing_manifest_or_unverified_archive_fails_clearly(env):
    env.files[1].write_bytes(env.files[1].read_bytes() + b"x")
    with pytest.raises(ex.ExtractionError, match="differs from the Zenodo manifest"):
        run(env)
    assert not (env.tmp / "processed").exists()
    env.zpath.unlink()
    code, msgs = run(env)
    assert code == 2 and any("not found" in x for x in msgs)


def test_licence_notes_mention_osm_landscan_and_record_licence(env):
    zen = json.loads(env.zpath.read_text(encoding="utf-8"))
    notes = " ".join(pg.licence_notes(zen, []))
    assert "ODbL" in notes and "LandScan" in notes and "cc-by-4.0" in notes and "NOT established" in notes
