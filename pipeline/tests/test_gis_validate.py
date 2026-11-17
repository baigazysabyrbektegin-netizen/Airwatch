import geopandas as gpd
import numpy as np
import pyproj
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from pipeline import gis_validate as gv


def gdf(geoms, crs=None):
    return gpd.GeoDataFrame({"id": range(len(geoms))}, geometry=geoms, crs=crs)


# ------------------------------------------------------------------ CRS detection and transformation
def test_declared_wgs84_is_kept():
    a = gv.assess_crs(gdf([Point(74.6, 42.9)], 4326), "x")
    assert a["status"] == "declared_wgs84" and a["declared_epsg"] == 4326 and a["note"] == ""
    assert gv.to_wgs84(gdf([Point(74.6, 42.9)], 4326), a).crs.to_epsg() == 4326


def test_undeclared_geographic_coordinates_are_an_assumption_with_evidence():
    g = gdf([Point(74.6, 42.9), Point(74.7, 43.0)])
    a = gv.assess_crs(g, "grid.shp", evidence=["matches the KML copy"])
    assert a["status"] == "assumed_wgs84_undeclared" and a["declared_crs"] is None
    assert a["evidence"] == ["matches the KML copy"] and "NOT declared" in a["note"]
    assert gv.to_wgs84(g, a).crs.to_epsg() == 4326
    assert g.crs is None                                    # the input object is not modified


def test_undeclared_coordinates_outside_the_study_area_are_not_assumed():
    far = gdf([Point(10.0, 50.0)])                          # plausible degrees, but nowhere near Bishkek
    assert gv.assess_crs(far, "x")["status"] == "unresolved"
    metric = gdf([Point(450000.0, 4750000.0)])             # projected-looking numbers, no CRS
    a = gv.assess_crs(metric, "metric.shp")
    assert a["status"] == "unresolved" and not a["coordinates_look_geographic"]
    with pytest.raises(gv.GisError, match="Cannot determine"):
        gv.to_wgs84(metric, a)


def test_declared_projected_crs_is_transformed_to_wgs84():
    lon, lat = 74.6, 42.9
    x, y = pyproj.Transformer.from_crs(4326, 32643, always_xy=True).transform(lon, lat)
    g = gdf([Point(x, y)], 32643)
    a = gv.assess_crs(g, "utm.shp")
    assert a["status"] == "declared_other" and a["declared_epsg"] == 32643
    out = gv.to_wgs84(g, a)
    assert out.crs.to_epsg() == 4326 and out.geometry.iloc[0].x == pytest.approx(lon, abs=1e-7) and out.geometry.iloc[0].y == pytest.approx(lat, abs=1e-7)


def test_assess_crs_handles_empty_input():
    a = gv.assess_crs(gdf([None, None]), "empty")
    assert a["status"] == "unresolved" and np.isnan(a["bounds"]).all()


# ------------------------------------------------------------------ geometry validation
def test_invalid_null_empty_and_duplicate_geometries_are_flagged_never_removed():
    bowtie = Polygon([(74.4, 42.8), (74.5, 42.9), (74.5, 42.8), (74.4, 42.9)])
    square = box(74.4, 42.8, 74.5, 42.9)
    g = gdf([bowtie, square, square, None, Polygon()])
    before = [None if x is None else x.wkb for x in g.geometry]
    out, rep = gv.validate_geometries(g)
    assert len(out) == 5 and out["geom_status"].tolist() == ["invalid", "ok", "ok", "null", "empty"]
    assert rep["features"] == 5 and rep["invalid"] == 1 and rep["null"] == 1 and rep["empty"] == 1 and rep["valid"] == 2
    assert rep["duplicate_geometries"] == 1 and "Self-intersection" in out["geom_invalid_reason"].iloc[0]
    assert [None if x is None else x.wkb for x in out.geometry] == before      # stored geometry untouched
    assert rep["geometry_types"]["Polygon"] == 4


def test_all_valid_has_no_flags():
    out, rep = gv.validate_geometries(gdf([Point(1, 1), LineString([(0, 0), (1, 1)])]))
    assert rep["invalid"] == rep["null"] == rep["empty"] == rep["duplicate_geometries"] == 0 and set(out["geom_status"]) == {"ok"}


def test_repair_for_overlay_does_not_mutate_the_original():
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    g = gdf([bowtie, None, box(0, 0, 1, 1)])
    fixed = gv.repaired_for_overlay(g)
    assert len(fixed) == 2 and fixed.geometry.is_valid.all()          # the null row is excluded, the bowtie is made valid
    assert not g.geometry.iloc[0].is_valid                              # original still invalid


def test_duplicate_ids():
    import pandas as pd

    df = pd.DataFrame({"osm_id": ["1", "2", "2", None, None]})
    assert gv.duplicate_ids(df, "osm_id") == 1 and gv.duplicate_ids(df, "missing") == 0
