"""CRS assessment and geometry validation for the spatial datasets.

Nothing is dropped or silently repaired: problems are flagged in columns and counted in reports.
A CRS is only *assumed* (never invented) when the source declares none, the coordinates are plausible
longitude/latitude values inside the study area, and the caller can supply supporting evidence.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

WGS84 = "EPSG:4326"
UTM43N = "EPSG:32643"          # UTM zone 43N: metric CRS used for lengths and areas in the Bishkek area
STUDY_BOX = (74.35, 42.70, 74.85, 43.10)   # lon_min, lat_min, lon_max, lat_max of the Zenodo airshed grid


class GisError(Exception):
    pass


def assess_crs(gdf: gpd.GeoDataFrame, name: str, box: tuple = STUDY_BOX, evidence: list[str] | None = None) -> dict:
    """Describe the declared CRS and decide whether the data can be treated as WGS84.

    status: declared_wgs84 | declared_other | assumed_wgs84_undeclared | unresolved"""
    declared = gdf.crs.to_string() if gdf.crs is not None else None
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    b = [float(v) for v in gdf.total_bounds] if len(gdf) and not gdf.geometry.isna().all() else [np.nan] * 4
    finite = bool(np.all(np.isfinite(b)))
    geographic = finite and -180 <= b[0] <= b[2] <= 180 and -90 <= b[1] <= b[3] <= 90
    overlaps = finite and b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1]
    if declared is not None and epsg == 4326:
        status = "declared_wgs84"
    elif declared is not None:
        status = "declared_other"
    elif geographic and overlaps:
        status = "assumed_wgs84_undeclared"
    else:
        status = "unresolved"
    return {"dataset": name, "declared_crs": declared, "declared_epsg": epsg, "status": status, "bounds": b,
            "coordinates_look_geographic": bool(geographic), "overlaps_study_area": bool(overlaps),
            "evidence": list(evidence or []),
            "note": ("CRS is NOT declared in the source; WGS84 is an assumption supported by the evidence listed."
                     if status == "assumed_wgs84_undeclared" else "")}


def to_wgs84(gdf: gpd.GeoDataFrame, assessment: dict) -> gpd.GeoDataFrame:
    """Return the data in WGS84. Undeclared-but-assumed data get the CRS set; declared other CRSs are transformed."""
    st = assessment["status"]
    if st == "unresolved":
        raise GisError(f"Cannot determine a usable CRS for {assessment['dataset']} (bounds {assessment['bounds']}).")
    if st == "assumed_wgs84_undeclared":
        return gdf.set_crs(WGS84, allow_override=False)
    return gdf if st == "declared_wgs84" else gdf.to_crs(WGS84)


def validate_geometries(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    """Flag null, empty and invalid geometries and count exact duplicates. Rows are never removed."""
    g = gdf.copy()
    geom = g.geometry
    null = geom.isna()
    empty = geom.is_empty & ~null
    valid = shapely.is_valid(geom.values)
    invalid = ~null & ~empty & ~pd.Series(valid, index=g.index)
    status = np.where(null, "null", np.where(empty, "empty", np.where(invalid, "invalid", "ok")))
    g["geom_status"] = status
    reason = pd.Series("", index=g.index, dtype="object")
    if invalid.any():
        reason[invalid] = [shapely.is_valid_reason(x) for x in geom[invalid]]
    g["geom_invalid_reason"] = reason
    ok = g[~null]
    dup = int(ok.geometry.to_wkb().duplicated().sum()) if len(ok) else 0
    report = {"features": int(len(g)), "null": int(null.sum()), "empty": int(empty.sum()), "invalid": int(invalid.sum()),
              "valid": int((status == "ok").sum()), "duplicate_geometries": dup,
              "invalid_reasons": reason[invalid].value_counts().to_dict(), "geometry_types": geom[~null].geom_type.value_counts().to_dict()}
    return g, report


def duplicate_ids(df: pd.DataFrame, col: str) -> int:
    return int(df[col].dropna().duplicated().sum()) if col in df.columns else 0


def repaired_for_overlay(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """A copy with invalid geometries made valid, used only for area calculations; the stored geometry is untouched."""
    g = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    bad = ~pd.Series(shapely.is_valid(g.geometry.values), index=g.index)
    if bad.any():
        g.loc[bad, "geometry"] = shapely.make_valid(g.loc[bad, "geometry"].values)
    return g
