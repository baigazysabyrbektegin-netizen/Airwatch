"""Processing of the Bishkek airshed spatial datasets onto the 0.01 degree reference grid.

All inputs come from Zenodo record 12720883. Every function returns the data plus a report dictionary
with the checks it ran; problems are reported, not hidden, and no record is dropped silently.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from pipeline import gis_validate as gv

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


@dataclass(frozen=True)
class GridSpec:
    """The Zenodo airshed grid: 50 x 40 cells of 0.01 degrees from (74.35 E, 42.70 N)."""

    nx: int = 50
    ny: int = 40
    step: float = 0.01
    lon0: float = 74.35
    lat0: float = 42.70

    def cell_id(self, ix, iy):
        return (iy - 1) * self.nx + ix

    def center(self, ix, iy):
        return self.lon0 + (ix - 0.5) * self.step, self.lat0 + (iy - 0.5) * self.step


# ----------------------------------------------------------------------------- reference grid
def load_grid(shp: Path, spec: GridSpec, kml: Path | None = None) -> tuple[gpd.GeoDataFrame, dict]:
    raw = gpd.read_file(shp)
    need = ["Maille", "Maille_X", "Maille_Y", "X", "Y", "X1", "Y1", "X2", "Y2", "area_cell"]
    miss = [c for c in need if c not in raw.columns]
    if miss:
        raise gv.GisError(f"Grid shapefile lacks columns {miss}")
    g = raw.rename(columns={"Maille": "cell_id", "Maille_X": "ix", "Maille_Y": "iy", "area_cell": "area_cell_src_km2"})
    for c in ("X", "Y", "X1", "Y1", "X2", "Y2", "area_cell_src_km2"):
        g[c] = pd.to_numeric(g[c], errors="coerce")
    g = g.rename(columns={"X": "lon_c", "Y": "lat_c"})
    checks: dict = {"n_features": int(len(g)), "expected": spec.nx * spec.ny}
    checks["count_ok"] = len(g) == spec.nx * spec.ny
    checks["cell_id_unique"] = bool(g["cell_id"].is_unique)
    checks["ix_iy_unique"] = not g.duplicated(["ix", "iy"]).any()
    checks["ix_range_ok"] = bool(g["ix"].between(1, spec.nx).all())
    checks["iy_range_ok"] = bool(g["iy"].between(1, spec.ny).all())
    checks["cell_id_formula_ok"] = bool((g["cell_id"] == spec.cell_id(g["ix"], g["iy"])).all())
    checks["cell_size_ok"] = bool(np.allclose(g["X2"] - g["X1"], spec.step, atol=1e-6) and np.allclose(g["Y2"] - g["Y1"], spec.step, atol=1e-6))
    cx, cy = spec.center(g["ix"], g["iy"])
    checks["centres_match_lattice"] = bool(np.allclose(g["lon_c"], cx, atol=1e-6) and np.allclose(g["lat_c"], cy, atol=1e-6))
    b = g.geometry.bounds
    checks["polygons_match_attribute_bounds"] = bool(np.allclose(b.minx, g["X1"], atol=1e-6) and np.allclose(b.maxx, g["X2"], atol=1e-6)
                                                      and np.allclose(b.miny, g["Y1"], atol=1e-6) and np.allclose(b.maxy, g["Y2"], atol=1e-6))
    evidence = []
    if checks["polygons_match_attribute_bounds"] and checks["centres_match_lattice"]:
        evidence.append("polygon coordinates equal the cell-corner attributes in degrees (74.35-74.85 E, 42.70-43.10 N)")
    if kml is not None and Path(kml).exists():
        k = gpd.read_file(kml)
        def centroids(gs):  # planar centroids of tiny cells in degrees; used only to match two copies of the same grid
            c = shapely.centroid(gs.geometry.values)
            return set(zip(np.round(shapely.get_x(c), 5), np.round(shapely.get_y(c), 5)))

        sc, kc = centroids(g), centroids(k)
        checks["kml_counterpart_features"] = int(len(k))
        checks["kml_centroids_matched"] = int(len(sc & kc))
        if len(k) == len(g) and sc == kc:
            evidence.append("the same grid in grids_bishkek.kml (KML is always WGS84) has identical cell centroids")
    assessment = gv.assess_crs(g, "grids_bishkek.shp", evidence=evidence)
    g = gv.to_wgs84(g, assessment)
    g["area_km2"] = g.to_crs(gv.UTM43N).area / 1e6
    checks["area_km2_range"] = [float(g["area_km2"].min()), float(g["area_km2"].max())]
    checks["area_cell_src_vs_computed_max_abs_diff_km2"] = float((g["area_km2"] - g["area_cell_src_km2"]).abs().max())
    g, geom = gv.validate_geometries(g)
    g = g.sort_values("cell_id").reset_index(drop=True)
    cols = ["cell_id", "ix", "iy", "lon_c", "lat_c", "area_cell_src_km2", "area_km2", "geom_status", "geometry"]
    ok = all(v for k, v in checks.items() if k.endswith("_ok") or k in ("cell_id_unique", "ix_iy_unique", "centres_match_lattice", "polygons_match_attribute_bounds"))
    return g[cols], {"checks": checks, "crs": assessment, "geometry": geom, "all_checks_passed": bool(ok), "resolution_deg": spec.step,
                     "approx_cell_size_km": [spec.step * 111.32 * np.cos(np.radians(g["lat_c"].mean())), spec.step * 110.57]}


# ----------------------------------------------------------------------------- population
def parse_population(csv: Path, grid: gpd.GeoDataFrame, spec: GridSpec) -> tuple[pd.DataFrame, dict]:
    d = pd.read_csv(csv)
    if not {"midlong", "midlat"} <= set(d.columns):
        raise gv.GisError("Population CSV needs midlong and midlat columns")
    ycols = {c: int(m.group(1)) for c in d.columns if (m := re.fullmatch(r"popgrid_bishkek_(\d{4})", c))}
    if not ycols:
        raise gv.GisError("Population CSV has no popgrid_bishkek_YYYY columns")
    d["ix"] = np.rint((d["midlong"] - spec.lon0) / spec.step - 0.5).astype(int) + 1
    d["iy"] = np.rint((d["midlat"] - spec.lat0) / spec.step - 0.5).astype(int) + 1
    cx, cy = spec.center(d["ix"], d["iy"])
    off = (np.abs(d["midlong"] - cx) > 1e-6) | (np.abs(d["midlat"] - cy) > 1e-6)
    d = d.rename(columns={c: f"pop_{y}" for c, y in ycols.items()})
    pcols = [f"pop_{y}" for y in sorted(ycols.values())]
    dup = int(d.duplicated(["ix", "iy"]).sum())
    merged = grid[["cell_id", "ix", "iy", "lon_c", "lat_c"]].merge(d[["ix", "iy"] + pcols].drop_duplicates(["ix", "iy"]), on=["ix", "iy"], how="left", indicator=True)
    years = sorted(ycols.values())
    per_year = {}
    for y in years:
        s = merged[f"pop_{y}"]
        per_year[y] = {"sum": float(s.sum()), "min": float(s.min()), "max": float(s.max()), "mean": float(s.mean()),
                       "zero_cells": int((s == 0).sum()), "negative": int((s < 0).sum()), "nan": int(s.isna().sum())}
    rep = {"rows_in_csv": int(len(d)), "years": years, "latest_year": years[-1], "midpoints_off_lattice": int(off.sum()), "duplicate_cells": dup,
           "grid_cells_without_population_row": int((merged["_merge"] == "left_only").sum()),
           "csv_rows_not_on_grid": int((~d.set_index(["ix", "iy"]).index.isin(grid.set_index(["ix", "iy"]).index)).sum()),
           "per_year": per_year,
           "unit_note": "No unit is stated in the file; values are treated as persons per cell (the 2019 sum is about 1.3 million for the 1,800 km2 airshed).",
           "source_note": "The accompanying slide deck names LandScan (ORNL) as the source; the Zenodo record metadata does not. Treat as unverified provenance."}
    return merged.drop(columns="_merge"), rep


# ----------------------------------------------------------------------------- CAMx
def parse_camx(csv: Path, grid: gpd.GeoDataFrame, spec: GridSpec) -> tuple[pd.DataFrame, dict]:
    d = pd.read_csv(csv)
    up = [m.upper() for m in MONTHS]
    need = ["ix", "iy"] + up
    miss = [c for c in need if c not in d.columns]
    if miss:
        raise gv.GisError(f"CAMx CSV lacks columns {miss}")
    d = d.rename(columns={m.upper(): f"camx_pm25_{m}" for m in MONTHS})
    mcols = [f"camx_pm25_{m}" for m in MONTHS]
    vals = d[mcols]
    rep = {"rows": int(len(d)), "expected_rows": spec.nx * spec.ny, "unique_cells": int(len(d.drop_duplicates(["ix", "iy"]))),
           "ix_range": [int(d["ix"].min()), int(d["ix"].max())], "iy_range": [int(d["iy"].min()), int(d["iy"].max())],
           "nan": int(vals.isna().sum().sum()), "negative": int((vals < 0).sum().sum()), "months": 12}
    merged = grid[["cell_id", "ix", "iy"]].merge(d, on=["ix", "iy"], how="left")
    rep["grid_cells_without_value"] = int(merged[mcols[0]].isna().sum())
    rep["rows_not_on_grid"] = int((~d.set_index(["ix", "iy"]).index.isin(grid.set_index(["ix", "iy"]).index)).sum())
    merged["camx_pm25_annual_mean"] = merged[mcols].mean(axis=1)
    rep["annual_mean_definition"] = "unweighted mean of the 12 monthly means"
    rep["domain_monthly_mean"] = {m: float(merged[f"camx_pm25_{m}"].mean()) for m in MONTHS}
    rep["monthly_min_max"] = {m: [float(merged[f"camx_pm25_{m}"].min()), float(merged[f"camx_pm25_{m}"].max())] for m in MONTHS}
    a = merged["camx_pm25_annual_mean"]
    rep["annual_mean"] = {"domain_mean": float(a.mean()), "min": float(a.min()), "median": float(a.median()), "max": float(a.max()),
                          "cell_of_max": [int(merged.loc[a.idxmax(), "ix"]), int(merged.loc[a.idxmax(), "iy"])]}
    rep["unit"] = "ug/m3 (stated in the Zenodo record description)"
    rep["data_kind"] = "MODELLED (WRF-CAMx output), not a measurement"
    rep["year_note"] = "The CSV does not state the year; the accompanying figures are named 2018 and the paper reports a 2018-2019 modelling period. Unverified."
    return merged, rep


# ----------------------------------------------------------------------------- emissions
EMI_COLS = ["ix", "iy", "midlong", "midlat", "FRPM", "CRPM", "PEC", "POA", "NO", "NO2", "CO", "SO2"]
SPECIES = ["FRPM", "CRPM", "PEC", "POA", "NO", "NO2", "CO", "SO2"]
PT_BASE = ["ix", "iy", "lon", "lat", "frpm", "crpm", "pec", "poa", "no", "no2", "unspecified_7", "unspecified_8", "unspecified_9", "co", "so2"]
PT_STACK = ["stack_param_1", "stack_param_2", "stack_param_3", "stack_param_4"]
# Sector prefixes come from the file names. The labels are INFERRED from those names and the category from the
# paper's Table 2 caption where it states one; neither is documented in the Zenodo record itself.
SECTOR_META = {
    "airport": ("Airport landing/take-off", "transport"), "brick": ("Brick kilns", "industry"), "const": ("Construction dust", "dust"),
    "cook": ("Household cooking", "residential"), "dgsets": ("Diesel generator sets", "unassigned"), "evap": ("Evaporative fuel emissions", "transport"),
    "heat": ("Space heating", "residential"), "indheavy": ("Heavy industry (includes point sources)", "industry"), "indlight": ("Light industry", "industry"),
    "light": ("Kerosene lighting", "unassigned"), "quarry": ("Quarries", "unassigned"), "rdust": ("Road dust", "dust"),
    "vehexh": ("Vehicle exhaust", "transport"), "waste": ("Waste", "waste"),
}
_EMI_NAME = re.compile(r"^bishkek_2018_([a-z0-9]+)_(.+)\.(emi|ptemi)$")


def split_name(filename: str) -> tuple[str, str]:
    m = _EMI_NAME.match(filename)
    if not m:
        raise gv.GisError(f"Unrecognised emissions file name: {filename}")
    return m.group(1), m.group(2)


def parse_emi(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip().strip('"')
    if header.split(",") != EMI_COLS:
        raise gv.GisError(f"{Path(path).name}: unexpected header {header!r}")
    return pd.read_csv(path, skiprows=1, header=None, names=EMI_COLS)


def parse_ptemi(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, header=None)
    if d.shape[1] == len(PT_BASE):
        d.columns = PT_BASE
    elif d.shape[1] == len(PT_BASE) + len(PT_STACK):
        d.columns = PT_BASE + PT_STACK
    else:
        raise gv.GisError(f"{Path(path).name}: {d.shape[1]} columns, expected {len(PT_BASE)} or {len(PT_BASE) + len(PT_STACK)}")
    return d


def read_emissions_area(folder: Path, grid: gpd.GeoDataFrame, spec: GridSpec) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    """Return (wide per-cell table, per-file sector table, report, {(sector, sub): cell-indexed species table})."""
    files = sorted(Path(folder).glob("*.emi"))
    if not files:
        raise gv.GisError(f"No .emi files in {folder}")
    base = grid[["cell_id", "ix", "iy"]].set_index(["ix", "iy"])
    wide = pd.DataFrame(index=grid["cell_id"].to_numpy())
    wide.index.name = "cell_id"
    rows, tables = [], {}
    problems = {"row_count": [], "off_grid": [], "negative": [], "nan": [], "centre_mismatch": []}
    sum_cols = {k: np.zeros(len(grid)) for k in ("frpm", "crpm", "pec", "poa", "no", "no2", "co", "so2")}
    group_pm25: dict[str, np.ndarray] = {}
    for f in files:
        sector, sub = split_name(f.name)
        d = parse_emi(f)
        if len(d) != spec.nx * spec.ny:
            problems["row_count"].append(f.name)
        d = d.merge(grid[["cell_id", "ix", "iy", "lon_c", "lat_c"]], on=["ix", "iy"], how="left")
        if d["cell_id"].isna().any():
            problems["off_grid"].append(f.name)
        if (np.abs(d["midlong"] - d["lon_c"]) > 1e-6).any() or (np.abs(d["midlat"] - d["lat_c"]) > 1e-6).any():
            problems["centre_mismatch"].append(f.name)
        if (d[SPECIES] < 0).any().any():
            problems["negative"].append(f.name)
        if d[SPECIES].isna().any().any():
            problems["nan"].append(f.name)
        d = d.dropna(subset=["cell_id"])
        d["cell_id"] = d["cell_id"].astype(int)
        d = d.drop_duplicates("cell_id").set_index("cell_id").reindex(grid["cell_id"]).fillna({c: 0.0 for c in SPECIES})
        pm25 = d["FRPM"] + d["PEC"] + d["POA"]              # documented components: FRPM + PEC + POA
        pm10 = pm25 + d["CRPM"]
        wide[f"pm25_{sector}_{sub}_t_yr"] = pm25.to_numpy()
        group_pm25.setdefault(sector, np.zeros(len(grid)))
        group_pm25[sector] += pm25.to_numpy()
        for k in sum_cols:
            sum_cols[k] += d[k.upper()].to_numpy()
        tables[(sector, sub)] = d[SPECIES].copy()
        label, cat = SECTOR_META.get(sector, ("(unrecognised sector)", "unassigned"))
        rows.append({"file": f.name, "sector": sector, "subsector": sub, "sector_label_inferred": label, "paper_category_inferred": cat,
                     "n_cells_nonzero_pm25": int((pm25 > 0).sum()), "pm25_t_yr": float(pm25.sum()), "pm10_t_yr": float(pm10.sum()),
                     **{f"{c.lower()}_t_yr": float(d[c].sum()) for c in SPECIES}})
    for s, arr in group_pm25.items():
        wide[f"pm25_{s}_t_yr"] = arr
    pm25_total = sum(group_pm25.values())
    wide["pm25_total_t_yr"] = pm25_total
    wide["pm10_total_t_yr"] = pm25_total + sum_cols["crpm"]
    for k in ("frpm", "crpm", "pec", "poa", "no", "no2", "co", "so2"):
        wide[f"{k}_total_t_yr"] = sum_cols[k]
    wide = wide.reset_index().merge(grid[["cell_id", "ix", "iy", "area_km2"]], on="cell_id")
    wide["pm25_total_t_per_km2_yr"] = wide["pm25_total_t_yr"] / wide["area_km2"]
    wide = wide.drop(columns="area_km2")
    sec = pd.DataFrame(rows)
    by_group = sec.groupby("sector")["pm25_t_yr"].sum().sort_values(ascending=False)
    rep = {"area_files": len(files), "problems": problems, "pm25_total_t_yr": float(pm25_total.sum()), "pm10_total_t_yr": float(wide["pm10_total_t_yr"].sum()),
           "species_totals_t_yr": {k: float(v.sum()) for k, v in sum_cols.items()},
           "pm25_by_sector_group_t_yr": {k: float(v) for k, v in by_group.items()},
           "pm25_share_by_sector_group_pct": {k: float(100 * v / by_group.sum()) for k, v in by_group.items()},
           "pm25_formula": "FRPM + PEC + POA (the Zenodo description writes FPRM + BC + OC; same quantities under CAMx names)",
           "units": "tons per grid cell per year (as stated in the Zenodo record description)", "vintage": "2018 base year",
           "data_kind": "MODELLED bottom-up emissions inventory, not a measurement",
           "paper_reference_total_t_yr": 5500, "cells_with_pm25": int((pm25_total > 0).sum())}
    return wide, sec, rep, tables


def read_emissions_points(folder: Path, grid: gpd.GeoDataFrame, area_tables: dict, spec: GridSpec) -> tuple[gpd.GeoDataFrame, dict]:
    frames, recon = [], []
    for f in sorted(Path(folder).glob("*.ptemi")):
        sector, sub = split_name(f.name)
        d = parse_ptemi(f)
        d.insert(0, "source_row", np.arange(1, len(d) + 1))
        d.insert(0, "subsector", sub)
        d.insert(0, "sector", sector)
        d.insert(0, "source_file", f.name)
        cx, cy = spec.center(d["ix"], d["iy"])
        d["lonlat_in_cell"] = ((d["lon"] - (cx - spec.step / 2) >= -1e-9) & (d["lon"] - (cx + spec.step / 2) <= 1e-9)
                               & (d["lat"] - (cy - spec.step / 2) >= -1e-9) & (d["lat"] - (cy + spec.step / 2) <= 1e-9))
        d["pm25_t_yr"] = d["frpm"] + d["pec"] + d["poa"]
        d["pm10_t_yr"] = d["pm25_t_yr"] + d["crpm"]
        frames.append(d)
        area = area_tables.get((sector, sub))
        if area is not None:
            g = d.groupby(["ix", "iy"])[[c.lower() for c in SPECIES]].sum()
            a = area.copy()
            a.index = pd.MultiIndex.from_arrays([grid.set_index("cell_id").loc[a.index, "ix"], grid.set_index("cell_id").loc[a.index, "iy"]])
            a.columns = [c.lower() for c in SPECIES]
            j = a.join(g.add_suffix("_pt"), how="left").fillna(0)
            diffs = {c: float((j[c] - j[c + "_pt"]).abs().max()) for c in a.columns}
            scale = float(max(1.0, j[list(a.columns)].abs().max().max()))
            recon.append({"file": f.name, "sector": sector, "subsector": sub, "rows": int(len(d)), "cells_with_points": int(len(g)),
                          "max_abs_diff_t_yr": max(diffs.values()), "reconciles_with_area_file": bool(max(diffs.values()) <= 1e-6 * scale),
                          "pm25_points_t_yr": float(d["pm25_t_yr"].sum()), "pm25_area_file_t_yr": float((a["frpm"] + a["pec"] + a["poa"]).sum()),
                          "columns": int(len(d.columns)), "unspecified_columns_sum": float(d[["unspecified_7", "unspecified_8", "unspecified_9"]].sum().sum()),
                          "lonlat_in_cell": int(d["lonlat_in_cell"].sum())})
    if not frames:
        return gpd.GeoDataFrame({"source_file": []}, geometry=gpd.GeoSeries([], crs=gv.WGS84), crs=gv.WGS84), {"files": 0, "rows": 0, "reconciliation": []}
    pts = pd.concat(frames, ignore_index=True)
    bad = ~(pts["lon"].between(-180, 180) & pts["lat"].between(-90, 90)) | pts[["lon", "lat"]].isna().any(axis=1)
    geom = gpd.points_from_xy(pts["lon"].where(~bad), pts["lat"].where(~bad))
    g = gpd.GeoDataFrame(pts, geometry=geom, crs=gv.WGS84)
    g, geo = gv.validate_geometries(g)
    rep = {"files": len(frames), "rows": int(len(g)), "missing_coordinates": int(bad.sum()), "geometry": geo, "reconciliation": recon,
           "all_reconcile": bool(all(r["reconciles_with_area_file"] for r in recon)) if recon else None,
           "stack_parameter_note": "Only the heavy-industry file has four extra trailing columns (values like 50, 2, 10, 400). They are undocumented; CAMx convention would suggest stack height (m), "
                                   "diameter (m), velocity (m/s) and temperature (K) but the order and units are NOT verified, so they are kept as stack_param_1..4.",
           "unspecified_columns_note": "Three columns between NO2 and CO are all zero in every file; their species are not documented.",
           "double_counting_warning": "The .emi grid files already contain these point sources (the point rows sum exactly to the .emi cell values), so point and area PM2.5 must NOT be added together."}
    return g, rep


# ----------------------------------------------------------------------------- roads
ROAD_GROUPS = {
    "primary": ["primary", "primary_link", "trunk", "trunk_link"],
    "secondary": ["secondary", "secondary_link"],
    "tertiary": ["tertiary", "tertiary_link"],
    "local_streets": ["residential", "living_street", "service"],
    "unclassified": ["unclassified", "unknown"],
    "non_motorised_ways": ["footway", "track", "track_grade1", "track_grade2", "track_grade3", "track_grade4", "track_grade5", "path", "pedestrian", "cycleway", "steps"],
}
_FCLASS_TO_GROUP = {f: g for g, fs in ROAD_GROUPS.items() for f in fs}
SRC_CSV_MAP = {"primary": "src_csv_km_primary", "secondary": "src_csv_km_secondary", "tertiary": "src_csv_km_tertiary_broad",
               "unclassified": "src_csv_km_unclassified", "Nomotorway": "src_csv_km_non_motorised_ways", "Total": "src_csv_km_total"}


def classify_road(fclass: pd.Series) -> pd.Series:
    return fclass.map(_FCLASS_TO_GROUP).fillna("other_unlisted")


def road_density(roads: gpd.GeoDataFrame, grid: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict]:
    """Road LENGTH per cell, clipped to cell polygons in UTM 43N. This is not traffic volume."""
    r = gv.repaired_for_overlay(roads)[["fclass", "geometry"]].copy()
    r["road_group"] = classify_road(r["fclass"])
    g = grid[["cell_id", "area_km2", "geometry"]].to_crs(gv.UTM43N)
    with warnings.catch_warnings():
        # A line that only touches a cell border intersects it in a point (zero length); keep_geom_type drops those on purpose.
        warnings.filterwarnings("ignore", message=".*keep_geom_type.*")
        ov = gpd.overlay(r.to_crs(gv.UTM43N), g[["cell_id", "geometry"]], how="intersection", keep_geom_type=True)
    ov["km"] = ov.geometry.length / 1000.0
    piv = ov.pivot_table(index="cell_id", columns="road_group", values="km", aggfunc="sum").reindex(grid["cell_id"]).fillna(0.0)
    out = grid[["cell_id", "ix", "iy"]].copy().set_index("cell_id")
    groups = list(ROAD_GROUPS) + (["other_unlisted"] if "other_unlisted" in piv.columns else [])
    area = grid.set_index("cell_id")["area_km2"]
    for gname in groups:
        km = piv[gname] if gname in piv.columns else 0.0
        out[f"road_length_km_{gname}"] = km
        out[f"road_density_km_per_km2_{gname}"] = km / area
    tot = piv.sum(axis=1)
    out["road_length_km_total"] = tot
    out["road_density_km_per_km2_total"] = tot / area
    rep = {"segments_input": int(len(roads)), "segments_used": int(len(r)), "total_length_km_clipped": float(ov["km"].sum()),
           "total_length_km_unclipped_utm": float(r.to_crs(gv.UTM43N).length.sum() / 1000),
           "by_group": {k: {"features": int((r["road_group"] == k).sum()), "length_km": float(piv[k].sum()) if k in piv.columns else 0.0,
                            "mean_density_km_per_km2": float((piv[k] / area).mean()) if k in piv.columns else 0.0,
                            "max_density_km_per_km2": float((piv[k] / area).max()) if k in piv.columns else 0.0} for k in groups},
           "fclass_counts": {k: int(v) for k, v in r["fclass"].value_counts().items()},
           "unlisted_fclass": sorted(set(r.loc[r["road_group"] == "other_unlisted", "fclass"].dropna())),
           "cells_with_roads": int((tot > 0).sum()), "definition": "road length (km) clipped to each cell divided by the cell area (km2); NOT traffic volume or emissions"}
    return out.reset_index(), rep


def compare_source_density(dens: pd.DataFrame, csv: Path) -> tuple[pd.DataFrame, dict]:
    """Attach the source CSV (km of road per cell) and measure how well our geometry-derived lengths reproduce it."""
    s = pd.read_csv(csv).rename(columns={"Cell ID": "cell_id"})
    need = ["cell_id", "primary", "secondary", "tertiary", "unclassified", "Nomotorway", "Total"]
    miss = [c for c in need if c not in s.columns]
    if miss:
        raise gv.GisError(f"Source density CSV lacks columns {miss}")
    s = s[need].rename(columns=SRC_CSV_MAP)
    m = dens.merge(s, on="cell_id", how="left")
    ours = {
        "src_csv_km_primary": m["road_length_km_primary"], "src_csv_km_secondary": m["road_length_km_secondary"],
        "src_csv_km_tertiary_broad": m["road_length_km_tertiary"] + m["road_length_km_local_streets"],
        "src_csv_km_unclassified": m["road_length_km_unclassified"], "src_csv_km_non_motorised_ways": m["road_length_km_non_motorised_ways"],
        "src_csv_km_total": m["road_length_km_total"],
    }
    rep = {}
    for col, mine in ours.items():
        src = m[col]
        rep[col] = {"corr": float(np.corrcoef(src, mine)[0, 1]) if src.std() > 0 and mine.std() > 0 else None,
                    "mean_source": float(src.mean()), "mean_ours": float(mine.mean()), "max_abs_diff_km": float((src - mine).abs().max())}
    rep["interpretation"] = ("The source columns are kilometres of road per cell (not per km2). Their 'tertiary' equals OSM tertiary + residential + living_street + service, "
                             "and 'Nomotorway' equals footways, tracks, paths, pedestrian ways, cycleways and steps. Our classes are narrower; the source values are preserved as src_csv_*.")
    return m, rep


def check_cell_split_layer(split: gpd.GeoDataFrame, master: gpd.GeoDataFrame, grid: gpd.GeoDataFrame, classes: list[str], name: str) -> dict:
    """primary/secondary shapefiles are the master roads cut at cell borders and tagged with the cell id (Maille)."""
    ids_split, ids_master = set(split["osm_id"].astype(str)), set(master.loc[master["fclass"].isin(classes), "osm_id"].astype(str))
    cells = grid.set_index("cell_id")["geometry"]
    inside = 0
    for geom, c in zip(split.geometry, split["Maille"]):
        poly = cells.get(int(c))
        inside += bool(poly is not None and geom.buffer(1e-9).within(poly.buffer(1e-9)))
    L = split.to_crs(gv.UTM43N).length / 1000
    ratio = pd.to_numeric(split["length"], errors="coerce") / L
    return {"layer": name, "segments": int(len(split)), "distinct_osm_ids": len(ids_split), "osm_ids_equal_master_classes": ids_split == ids_master,
            "segments_inside_their_Maille_cell": int(inside), "length_attribute_over_utm_km_median_ratio": float(ratio.median()),
            "length_attribute_unit_note": "the 'length' attribute is about 0.011 x the length in km, i.e. it is in degrees (planar length in WGS84 units), not km or m; it is not used"}


# ----------------------------------------------------------------------------- context layers
CONTEXT_META = {
    "brick_kilns": ("Brick kilns", "point", "inferred_from_file_name", "16 points; the paper mentions 16 large open brick kilns"),
    "bte_boilers": ("Heat-only boilers (BTE list)", "point", "inferred_from_file_name", "28 named points; the slide deck says the list was extracted from a World Bank (2015) report"),
    "indus_area1": ("Industrial areas (set 1)", "polygon", "documented_in_record", "record lists 'industrial_areas'"),
    "indus_area2": ("Industrial areas (set 2)", "polygon", "documented_in_record", "record lists 'industrial_areas'"),
    "landfills": ("Landfill", "polygon", "documented_in_record", "record lists 'landfill'"),
    "quarries": ("Quarries", "polygon", "documented_in_record", "record lists 'quarries'"),
    "water_bodies": ("Water bodies", "polygon", "documented_in_record", "record lists 'water_bodies'"),
    "main_roads": ("Main roads (hand-digitised lines)", "line", "documented_in_record", "record lists 'main_roads'; only 2 of 17 lines carry a name (A365, M39)"),
    "dh_districts": ("Possibly district-heating area", "polygon", "unverified", "the record lists 'districts, townships' without saying which file is which; the name suggests district heating"),
    "main_province": ("Main urban/province area (meaning unverified)", "polygon", "unverified", "the record lists 'districts, townships' without saying which file is which"),
}
KML_STYLE_COLUMNS = ["description", "timestamp", "begin", "end", "altitudeMode", "tessellate", "extrude", "visibility", "drawOrder", "icon", "id"]


def read_context_layers(folder: Path) -> tuple[gpd.GeoDataFrame, dict]:
    frames, reports = [], {}
    for f in sorted(Path(folder).glob("*.kml")):
        layers = [row[0] for row in pyogrio.list_layers(f) if row[0] != "Unknown"]
        for lay in layers:
            g = gpd.read_file(f, layer=lay)
            assessment = gv.assess_crs(g, f.name)
            g = gv.to_wgs84(g, assessment)
            g, geo = gv.validate_geometries(g)
            label, kind, status, basis = CONTEXT_META.get(f.stem, (f.stem, "unknown", "unverified", "no documentation"))
            name = g["Name"].astype(str) if "Name" in g.columns else pd.Series("", index=g.index)
            placeholder = name.str.startswith("Untitled") | (name == "")
            out = gpd.GeoDataFrame({
                "layer": f.stem, "layer_label": label, "layer_meaning_status": status, "source_file": f.name,
                "feature_index": np.arange(1, len(g) + 1), "feature_name": name.where(~placeholder, None),
                "name_is_placeholder": placeholder.to_numpy(), "geom_status": g["geom_status"].to_numpy(),
                "geom_invalid_reason": g["geom_invalid_reason"].to_numpy()}, geometry=g.geometry.to_numpy(), crs=gv.WGS84)
            frames.append(out)
            dropped = [c for c in KML_STYLE_COLUMNS if c in g.columns]
            reports[f.stem] = {"features": int(len(g)), "geometry_kind": kind, "crs": assessment, "geometry": geo, "meaning_status": status, "meaning_basis": basis,
                               "named_features": int((~placeholder).sum()), "placeholder_names": int(placeholder.sum()), "kml_presentation_columns_dropped": dropped,
                               "bounds": [float(v) for v in g.total_bounds]}
    if not frames:
        raise gv.GisError(f"No KML layers found in {folder}")
    return pd.concat(frames, ignore_index=True).set_crs(gv.WGS84, allow_override=True), reports


def context_grid(ctx: gpd.GeoDataFrame, grid: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict]:
    """Counts of point features and area fractions of polygon layers per cell (cell-level context only)."""
    cells = grid[["cell_id", "area_km2", "geometry"]].to_crs(gv.UTM43N)
    out = grid[["cell_id"]].copy().set_index("cell_id")
    c = ctx[ctx["geom_status"] == "ok"].to_crs(gv.UTM43N)
    for layer, col in (("bte_boilers", "n_bte_boilers"), ("brick_kilns", "n_brick_kilns")):
        pts = c[c["layer"] == layer]
        j = gpd.sjoin(pts[["geometry"]], cells[["cell_id", "geometry"]], predicate="within", how="inner") if len(pts) else None
        out[col] = (j.groupby("cell_id").size() if j is not None else pd.Series(dtype=int)).reindex(out.index).fillna(0).astype(int)
    area = cells.set_index("cell_id")["area_km2"]
    polys = {"frac_industrial_areas": ["indus_area1", "indus_area2"], "frac_quarries": ["quarries"], "frac_water_bodies": ["water_bodies"], "frac_landfill": ["landfills"],
             "frac_dh_districts_unverified": ["dh_districts"], "frac_main_province_unverified": ["main_province"]}
    for col, layers in polys.items():
        sub = c[c["layer"].isin(layers)]
        if sub.empty:
            out[col] = 0.0
            continue
        u = gv.repaired_for_overlay(sub).geometry.union_all()
        ov = gpd.overlay(gpd.GeoDataFrame({"geometry": [u]}, crs=gv.UTM43N), cells[["cell_id", "geometry"]], how="intersection")
        frac = (ov.geometry.area / 1e6).groupby(ov["cell_id"]).sum() / area
        out[col] = frac.reindex(out.index).fillna(0.0).clip(0, 1)
    rep = {"cells_with_boilers": int((out["n_bte_boilers"] > 0).sum()), "boilers_counted": int(out["n_bte_boilers"].sum()), "cells_with_brick_kilns": int((out["n_brick_kilns"] > 0).sum()),
           "kilns_counted": int(out["n_brick_kilns"].sum()), **{f"{k}_cells_nonzero": int((out[k] > 0).sum()) for k in polys},
           **{f"{k}_total_km2": float((out[k] * area).sum()) for k in polys},
           "note": "Boilers/kilns outside the grid box (if any) are not counted; polygon fractions use the union of each layer so overlaps are not double counted."}
    return out.reset_index(), rep


def read_admin(shp: Path, grid: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, dict]:
    a = gpd.read_file(shp)
    assessment = gv.assess_crs(a, Path(shp).name)
    a = gv.to_wgs84(a, assessment)
    a, geo = gv.validate_geometries(a)
    box = grid.to_crs(gv.UTM43N).geometry.union_all()
    u = a.to_crs(gv.UTM43N).geometry.union_all()
    names = a["Name"].astype(str).unique().tolist() if "Name" in a.columns else []
    desc = a["descriptio"].astype(str).unique().tolist() if "descriptio" in a.columns else []
    out = gpd.GeoDataFrame({"admin_code": a.get("Name"), "admin_name": a.get("descriptio"), "feature_index": np.arange(1, len(a) + 1),
                            "admin_level_inferred": "country (adm0)", "source_file": Path(shp).name, "geom_status": a["geom_status"].to_numpy()},
                           geometry=a.geometry.to_numpy(), crs=gv.WGS84)
    rep = {"features": int(len(a)), "crs": assessment, "geometry": geo, "distinct_codes": names, "distinct_names": desc,
           "union_area_km2": float(u.area / 1e6), "grid_box_km2": float(box.area / 1e6), "share_of_grid_box_covered_pct": float(100 * u.intersection(box).area / box.area),
           "level_note": ("The file is named 'bishkek_adm2' but every polygon is labelled KGZ / Kyrgyzstan: it is the country polygon clipped to the airshed box, "
                          "NOT Bishkek's districts or city boundary. No city or district boundary exists in the archive.")}
    return out, rep


def kgz_fraction(admin: gpd.GeoDataFrame, grid: gpd.GeoDataFrame) -> pd.DataFrame:
    cells = grid[["cell_id", "area_km2", "geometry"]].to_crs(gv.UTM43N)
    u = gv.repaired_for_overlay(admin).to_crs(gv.UTM43N).geometry.union_all()
    ov = gpd.overlay(gpd.GeoDataFrame({"geometry": [u]}, crs=gv.UTM43N), cells[["cell_id", "geometry"]], how="intersection")
    f = (ov.geometry.area / 1e6).groupby(ov["cell_id"]).sum().reindex(cells["cell_id"]).fillna(0.0) / cells.set_index("cell_id")["area_km2"]
    return pd.DataFrame({"cell_id": cells["cell_id"].to_numpy(), "frac_in_kgz_polygons": f.clip(0, 1).to_numpy()})


# ----------------------------------------------------------------------------- integration
def integrate(grid: gpd.GeoDataFrame, pop: pd.DataFrame, roads: pd.DataFrame, camx: pd.DataFrame, em_area: pd.DataFrame,
              em_points: gpd.GeoDataFrame, ctx: pd.DataFrame, kgz: pd.DataFrame, pop_year: int) -> tuple[gpd.GeoDataFrame, dict]:
    """One row per grid cell with the headline attributes of each source, each keeping its own vintage."""
    g = grid[["cell_id", "ix", "iy", "lon_c", "lat_c", "area_km2", "geometry"]].copy()
    g = g.merge(pop[["cell_id", f"pop_{pop_year}"]], on="cell_id", how="left")
    road_cols = [c for c in roads.columns if c.startswith("road_")]
    g = g.merge(roads[["cell_id"] + road_cols], on="cell_id", how="left")
    camx_cols = [f"camx_pm25_{m}" for m in MONTHS] + ["camx_pm25_annual_mean"]
    g = g.merge(camx[["cell_id"] + camx_cols], on="cell_id", how="left")
    groups = [c for c in em_area.columns if re.fullmatch(r"pm25_[a-z0-9]+_t_yr", c) and c != "pm25_total_t_yr"]   # one column per sector group
    em_cols = groups + ["pm25_total_t_yr", "pm25_total_t_per_km2_yr", "pm10_total_t_yr", "no_total_t_yr", "no2_total_t_yr", "co_total_t_yr", "so2_total_t_yr"]
    em = em_area[["cell_id"] + em_cols].rename(columns={c: f"emis_{c}" for c in em_cols})
    g = g.merge(em, on="cell_id", how="left")
    if len(em_points):
        agg = em_points.groupby(["ix", "iy"]).agg(n_point_sources=("source_row", "size"), emis_point_sources_pm25_t_yr=("pm25_t_yr", "sum")).reset_index()
        g = g.merge(agg, on=["ix", "iy"], how="left")
    else:
        g["n_point_sources"], g["emis_point_sources_pm25_t_yr"] = 0, 0.0
    g["n_point_sources"] = g["n_point_sources"].fillna(0).astype(int)
    g["emis_point_sources_pm25_t_yr"] = g["emis_point_sources_pm25_t_yr"].fillna(0.0)
    g = g.merge(ctx, on="cell_id", how="left").merge(kgz, on="cell_id", how="left")
    g = g.sort_values("cell_id").reset_index(drop=True)
    rep = {"rows": int(len(g)), "columns": int(len(g.columns)), "one_row_per_cell": bool(g["cell_id"].is_unique and len(g) == len(grid)),
           "missing_values_per_column": {c: int(g[c].isna().sum()) for c in g.columns if c != "geometry" and g[c].isna().any()},
           "population_year_used": pop_year}
    return gpd.GeoDataFrame(g, geometry="geometry", crs=gv.WGS84), rep
