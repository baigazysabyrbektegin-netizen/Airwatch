"""Tiny synthetic stand-ins for the Zenodo GIS archives (3 x 2 grid of 0.01 degree cells)."""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, box

from pipeline import gis_grid as gg
from pipeline import manifest as mf

SPEC = gg.GridSpec(nx=3, ny=2)
MONTHS_UP = [m.upper() for m in gg.MONTHS]


def cell_bounds(ix, iy, spec=SPEC):
    x0, y0 = spec.lon0 + (ix - 1) * spec.step, spec.lat0 + (iy - 1) * spec.step
    return x0, y0, x0 + spec.step, y0 + spec.step


def grid_source_frame(spec=SPEC, drop_cell: int | None = None) -> gpd.GeoDataFrame:
    """The grid shapefile as the source writes it: text attributes, no CRS."""
    rows, geoms = [], []
    for iy in range(1, spec.ny + 1):
        for ix in range(1, spec.nx + 1):
            cid = spec.cell_id(ix, iy)
            if cid == drop_cell:
                continue
            x0, y0, x1, y1 = cell_bounds(ix, iy, spec)
            cx, cy = spec.center(ix, iy)
            rows.append({"Maille": cid, "Maille_Y": iy, "Maille_X": ix, "X": f"{cx:.3f}", "Y": f"{cy:.3f}", "X1": f"{x0:.3f}", "Y1": f"{y0:.3f}",
                         "X2": f"{x1:.3f}", "Y2": f"{y1:.3f}", "area_cell": "0.908"})
            geoms.append(box(x0, y0, x1, y1))
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=None)


def _quiet_write(g, path, **kw):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g.to_file(path, **kw)


def write_grid(folder: Path, spec=SPEC, kml=True) -> gpd.GeoDataFrame:
    folder.mkdir(parents=True, exist_ok=True)
    g = grid_source_frame(spec)
    _quiet_write(g, folder / "grids_bishkek.shp")
    if kml:
        _quiet_write(g.set_crs(4326), folder / "grids_bishkek.kml", driver="KML", layer="grids_bishkek")
    return g


def write_population(path: Path, spec=SPEC, years=(2018, 2019), drop_cell=None, extra_rows=None, negative=False):
    rows = []
    for iy in range(1, spec.ny + 1):
        for ix in range(1, spec.nx + 1):
            cid = spec.cell_id(ix, iy)
            if cid == drop_cell:
                continue
            cx, cy = spec.center(ix, iy)
            row = {"midlong": round(cx, 3), "midlat": round(cy, 3)}
            for y in years:
                row[f"popgrid_bishkek_{y}"] = cid * 100 + (y - 2018) * 5 - (1000 if negative and cid == 2 else 0)
            rows.append(row)
    rows += extra_rows or []
    pd.DataFrame(rows).to_csv(path, index=False)


def write_camx(path: Path, spec=SPEC, drop_cell=None, negative=False):
    rows = []
    for iy in range(1, spec.ny + 1):
        for ix in range(1, spec.nx + 1):
            cid = spec.cell_id(ix, iy)
            if cid == drop_cell:
                continue
            row = {"ix": ix, "iy": iy}
            row.update({m: float(cid + i + 1 - (100 if negative and cid == 3 and i == 0 else 0)) for i, m in enumerate(MONTHS_UP)})
            rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def write_emi(folder: Path, name: str, values: dict, spec=SPEC, header=None, crlf=True):
    """values: {(ix, iy): {species: value}}; every cell gets a row (zeros elsewhere), like the source files."""
    hdr = header or '"ix,iy,midlong,midlat,FRPM,CRPM,PEC,POA,NO,NO2,CO,SO2"'
    lines = [hdr]
    for iy in range(1, spec.ny + 1):
        for ix in range(1, spec.nx + 1):
            cx, cy = spec.center(ix, iy)
            v = values.get((ix, iy), {})
            lines.append(",".join(str(x) for x in [ix, iy, round(cx, 3), round(cy, 3)] + [v.get(s, 0) for s in gg.SPECIES]))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(("\r\n" if crlf else "\n").join(lines).encode() + b"\r\n")


def write_ptemi(folder: Path, name: str, rows: list[list]):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(",".join(str(v) for v in r) for r in rows) + "\n", encoding="utf-8")


def pt_row(ix, iy, vals: dict, stack=None, spec=SPEC, dx=0.0):
    """One point-source row: ix, iy, lon, lat, FRPM..POA, NO, NO2, 3 unspecified zeros, CO, SO2 [, 4 stack values]."""
    cx, cy = spec.center(ix, iy)
    r = [ix, iy, round(cx + dx, 6), round(cy, 6)] + [vals.get(k, 0) for k in ("FRPM", "CRPM", "PEC", "POA", "NO", "NO2")] + [0, 0, 0] + [vals.get("CO", 0), vals.get("SO2", 0)]
    return r + (stack or [])


def write_emissions(folder: Path, spec=SPEC, break_reconciliation=False):
    write_emi(folder, "bishkek_2018_vehexh_cars.emi", {(1, 1): {"FRPM": 10, "CRPM": 4, "PEC": 2, "POA": 3, "NO": 50}, (2, 1): {"FRPM": 1, "PEC": 1, "POA": 1, "NO": 100}}, spec)
    write_emi(folder, "bishkek_2018_brick_coal.emi", {(3, 2): {"FRPM": 6, "CRPM": 1, "PEC": 2, "POA": 2, "NO": 5}}, spec)
    write_ptemi(folder, "bishkek_2018_brick_coal.ptemi",
                [pt_row(3, 2, {"FRPM": 4, "CRPM": 0.5, "PEC": 1, "POA": 1, "NO": 3}), pt_row(3, 2, {"FRPM": 2 + (1 if break_reconciliation else 0), "CRPM": 0.5, "PEC": 1, "POA": 1, "NO": 2})])
    write_emi(folder, "bishkek_2018_indheavy_indheavy.emi", {(2, 2): {"FRPM": 20, "CRPM": 5, "PEC": 5, "POA": 5, "NO": 7, "SO2": 9}}, spec)
    write_ptemi(folder, "bishkek_2018_indheavy_indheavy.ptemi", [pt_row(2, 2, {"FRPM": 20, "CRPM": 5, "PEC": 5, "POA": 5, "NO": 7, "SO2": 9}, stack=[50, 2, 10, 400])])
    (folder / "camxarea_monthly_profile.csv").write_text("MONTH_SHARES,SECTOR\n1,vehexh_cars\n", encoding="utf-8")


def roads_source_frame(spec=SPEC) -> gpd.GeoDataFrame:
    lon0, lat0 = spec.lon0, spec.lat0
    lines = [
        ("1", "primary", LineString([(lon0 + 0.005, lat0 + 0.005), (lon0 + 0.025, lat0 + 0.005)])),      # crosses cells 1,2,3 (1/4, 1/2, 1/4)
        ("2", "tertiary", LineString([(lon0 + 0.005, lat0 + 0.012), (lon0 + 0.005, lat0 + 0.018)])),     # inside cell 4
        ("3", "residential", LineString([(lon0 + 0.012, lat0 + 0.012), (lon0 + 0.018, lat0 + 0.012)])),  # inside cell 5
        ("4", "ferry", LineString([(lon0 + 0.022, lat0 + 0.012), (lon0 + 0.028, lat0 + 0.012)])),        # unlisted class, cell 6
        ("5", "secondary", LineString([(lon0 + 0.022, lat0 + 0.016), (lon0 + 0.028, lat0 + 0.016)])),    # cell 6
        ("6", "footway", None),                                                                           # missing geometry, must be kept
    ]
    return gpd.GeoDataFrame({"osm_id": [a for a, _, _ in lines], "code": 0, "fclass": [b for _, b, _ in lines], "name": "????", "ref": None, "oneway": "B",
                             "maxspeed": 0, "layer": 0, "bridge": "F", "tunnel": "F"}, geometry=[c for _, _, c in lines], crs=None)


def write_roads(folder: Path, spec=SPEC):
    folder.mkdir(parents=True, exist_ok=True)
    master = roads_source_frame(spec)
    _quiet_write(master[master.geometry.notna()], folder / "grids_bishkek_roads.shp")
    cells = grid_source_frame(spec)
    for name, classes in (("primary", gg.ROAD_GROUPS["primary"]), ("secondary", gg.ROAD_GROUPS["secondary"])):
        rows, geoms = [], []
        for _, r in master[master["fclass"].isin(classes) & master.geometry.notna()].iterrows():
            for _, c in cells.iterrows():
                part = r.geometry.intersection(c.geometry)
                if not part.is_empty:
                    rows.append({**{k: r[k] for k in ("osm_id", "code", "fclass", "name", "ref", "oneway", "maxspeed", "layer", "bridge", "tunnel")},
                                 "Maille": int(c["Maille"]), "length": part.length, "RoadType": name})
                    geoms.append(part)
        _quiet_write(gpd.GeoDataFrame(rows, geometry=geoms, crs=None), folder / f"grids_bishkek_{name}.shp")


def write_density_csv(folder: Path, dens: pd.DataFrame):
    pd.DataFrame({"Cell ID": dens["cell_id"], "Maille_X": dens["ix"], "Maille_Y": dens["iy"],
                  "Nomotorway": dens["road_length_km_non_motorised_ways"], "primary": dens["road_length_km_primary"],
                  "secondary": dens["road_length_km_secondary"], "tertiary": dens["road_length_km_tertiary"] + dens["road_length_km_local_streets"],
                  "unclassified": dens["road_length_km_unclassified"], "Total": dens["road_length_km_total"]}).to_csv(folder / "roads_density_byclass_bishkek.csv", index=False)


def write_admin(folder: Path, spec=SPEC):
    folder.mkdir(parents=True, exist_ok=True)
    x0, y0 = spec.lon0, spec.lat0
    g = gpd.GeoDataFrame({"Name": ["KGZ"], "descriptio": ["Kyrgyzstan"]}, geometry=[box(x0, y0, x0 + 0.025, y0 + 0.02)], crs=4326)
    _quiet_write(g, folder / "bishkek_adm2.shp")


def write_kmls(folder: Path, spec=SPEC):
    folder.mkdir(parents=True, exist_ok=True)
    x0, y0 = spec.lon0, spec.lat0

    def w(stem, names, geoms):
        _quiet_write(gpd.GeoDataFrame({"Name": names}, geometry=geoms, crs=4326), folder / f"{stem}.kml", driver="KML", layer=stem)

    w("bte_boilers", ["Boiler A", "Boiler B"], [Point(x0 + 0.005, y0 + 0.005), Point(x0 + 0.015, y0 + 0.005)])
    w("brick_kilns", ["Untitled Placemark", "Untitled Placemark"], [Point(x0 + 0.025, y0 + 0.015), Point(x0 + 0.5, y0 + 0.5)])    # second kiln is off the grid
    w("indus_area1", ["Untitled Polygon"], [box(x0, y0 + 0.01, x0 + 0.005, y0 + 0.02)])                                             # half of cell 4
    w("indus_area2", ["Untitled Polygon"], [box(x0, y0 + 0.01, x0 + 0.005, y0 + 0.02)])                                             # same half again
    w("water_bodies", ["Untitled Polygon"], [box(x0 + 0.01, y0 + 0.01, x0 + 0.02, y0 + 0.02)])                                      # all of cell 5
    w("dh_districts", ["Untitled Polygon"], [box(x0, y0, x0 + 0.03, y0 + 0.01)])


def make_tar(archive: Path, folder: Path):
    names = sorted(p.name for p in folder.iterdir() if p.is_file())
    archive.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["tar", "-cf", str(archive), "-C", str(folder), *names], check=True)


def build_inputs(tmp: Path, spec=SPEC, break_reconciliation=False) -> dict:
    """Create all synthetic archives plus a matching Zenodo manifest; returns paths."""
    src = tmp / "src"
    write_grid(src / "pop", spec)
    write_population(src / "pop" / "bishkek_pop_allyears.csv", spec)
    pd.DataFrame({"Maille": [1], "Maille_X": [1], "Maille_Y": [1], "X1": [0], "Y1": [0], "X2": [0], "Y2": [0], "X": [0], "Y": [0], "area_cell": [0]}).to_csv(src / "pop" / "grids_bishkek.csv", index=False)
    write_roads(src / "roads", spec)
    grid, _ = gg.load_grid(src / "pop" / "grids_bishkek.shp", spec)
    roads = gpd.read_file(src / "roads" / "grids_bishkek_roads.shp").set_crs(4326)
    roads["road_group"] = gg.classify_road(roads["fclass"])
    dens, _ = gg.road_density(roads, grid)
    write_density_csv(src / "roads", dens)
    write_admin(src / "adm", spec)
    write_kmls(src / "kml", spec)
    write_emissions(src / "emi", spec, break_reconciliation)
    raw = tmp / "raw"
    stems = {"gis_bishkek-grids-pop.rar": "pop", "gis_bishkek_roads.rar": "roads", "gis_bishkek_adm0.rar": "adm", "gis_bishkek_multiplelayers.rar": "kml",
             "bishkek_gridded_emissions_2018.rar": "emi"}
    files = []
    for arc, folder in stems.items():
        make_tar(raw / arc, src / folder)
        files.append(raw / arc)
    write_camx(raw / "bishkek_camx_pm25_monthlyavg.csv", spec)
    files.append(raw / "bishkek_camx_pm25_monthlyavg.csv")
    entries = []
    for p in files:
        h = mf.hash_file(p, ("md5", "sha256"))
        entries.append({"filename": p.name, "local_path": str(p), "download_status": "downloaded_verified", "size_bytes": p.stat().st_size,
                        "zenodo_checksum": {"algorithm": "md5", "value": h["md5"]}, "calculated_checksums": h, "provenance": {"review_required": True}})
    zen = {"record": {"record_id": 1, "doi": "10.0/x", "licence": {"id": "cc-by-4.0"}}, "files": entries}
    zpath = tmp / "zenodo.json"
    zpath.write_text(json.dumps(zen), encoding="utf-8")
    return {"zpath": zpath, "raw": raw, "src": src, "files": files}
