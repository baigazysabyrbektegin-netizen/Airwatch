"""Stage 4: process the Zenodo GIS, CAMx and emissions datasets onto the 0.01 degree reference grid.

Run from the repository root with the backend virtual environment:

    python -m pipeline.process_gis

Archives are checksum-verified against ``data/manifest/zenodo_12720883.json`` and extracted (never modified).
Outputs: ``data/interim/gis/`` (extracted, validated, reports) and ``data/processed/gis/``, with the
provenance record ``data/manifest/gis_processing.json``. An existing output that would change is never
overwritten unless ``--allow-overwrite`` is given.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow
import pyogrio
import pyproj
import shapely

from pipeline import embassy_extract as ex
from pipeline import gis_extract as gx
from pipeline import gis_grid as gg
from pipeline import gis_report as gr
from pipeline import gis_validate as gv
from pipeline import io_utils as io
from pipeline import manifest as mf

REPO_ROOT = io.REPO_ROOT
ZENODO_MANIFEST = REPO_ROOT / "data" / "manifest" / "zenodo_12720883.json"
PROCESSING_MANIFEST = REPO_ROOT / "data" / "manifest" / "gis_processing.json"
PROCESSING_VERSION = "gis-1.0.0"
SCRIPT = "pipeline/process_gis.py"

DECISIONS = [
    "Reference grid: the Zenodo 50 x 40 grid of 0.01 degree cells (cell_id = (iy-1)*50 + ix). Population, CAMx and the emissions tables are already on this grid; they are joined on the cell indices (ix, iy), never resampled.",
    "Population: the latest annual column (2019) is placed in the integrated grid; all 20 years stay in population_grid.parquet. Nothing is interpolated or projected to other years.",
    "Roads: OSM classes are grouped (primary incl. trunk and links; secondary; tertiary; local streets = residential, living_street, service; unclassified; non-motorised ways). Lines are clipped to cell polygons in UTM 43N and "
    "summed; density = length (km) / cell area (km2). The source CSV counts km per cell, and its 'tertiary' is broader than OSM tertiary, so its values are kept separately as src_csv_*.",
    "CAMx: monthly values are copied as given; the annual mean is the unweighted mean of the 12 monthly means. Nothing is smoothed or interpolated.",
    "Emissions: PM2.5 = FRPM + PEC + POA per sector file, summed by sector group and in total (tons per cell per year). The .emi files already include the point sources, so the point table is a subset and is never added.",
    "Context layers: point layers are counted per cell; polygon layers become area fractions of each cell (union per layer, UTM 43N). Layers with unverified meaning are labelled so.",
    "Administrative polygons: converted to a share of each cell lying inside the Kyrgyzstan polygons (frac_in_kgz_polygons).",
    "CAMx and the emissions inventory are related (the inventory is a model input); they are shown side by side but must not be read as independent evidence of each other.",
    "No weighting, scoring, normalisation or ranking is applied here; that belongs to the Monitoring Gap Planner stage.",
]
LIMITATIONS = [
    "Vintages differ and are not harmonised: population 2000-2019 (latest 2019), emissions 2018, CAMx year not stated in the file (figures named 2018), OSM roads and the KML layers have no stated date.",
    "The CRS is not declared for the grid, population and road shapefiles (no .prj). WGS84 is assumed on the evidence listed in section 3; a different datum would shift features slightly but not by cell-scale amounts.",
    "Population, CAMx and emissions are modelled/estimated products on a ~0.9 km2 grid. They are not measurements and are not more accurate for being integrated on one grid.",
    "Road density is road length per area. It says nothing about traffic volume, vehicle type or emissions.",
    "The source road density CSV is in km per cell and its 'tertiary' class is broader than OSM tertiary; do not compare the two without reading section 10.",
    "Road names and references in the shapefiles consist of question marks: the Cyrillic text was already lost in the source DBF files. They are kept as given and are not usable.",
    "The administrative file is the Kyrgyzstan country polygon clipped to the airshed box (it is named adm2 but is labelled KGZ / Kyrgyzstan throughout). There is no Bishkek city or district boundary in the archive.",
    "KML context layers have almost no attributes (only boilers are named). The meaning of dh_districts and main_province is unverified; brick_kilns and bte_boilers are identified only by file name, the paper and the deck.",
    "The emissions .emi files contain the point sources; the three 'unspecified' point-source columns are all zero; the four stack parameters (heavy industry only) are undocumented and kept unlabelled.",
    "Sector names are inferred from file names; the Zenodo record does not define them. The 'heat' sector may not include the central heating plant (probably inside heavy industry); this was not verified.",
    "Cells at the northern edge may lie partly outside Kyrgyzstan (see frac_in_kgz_polygons); the datasets do not say how such cells were treated.",
]


def licence_notes(zen: dict, extraction: list[dict]) -> list[str]:
    rec = zen["record"]
    notes = [f"Zenodo record {rec['record_id']} ({rec['doi']}) is licensed {rec['licence']} at record level; attribution is required: "
             "Guttikunda, S. (2024). A Multi-Pollutant Emissions Inventory for Air Pollution Modeling and Supporting Information for Bishkek. Zenodo. doi:10.5281/zenodo.12720883.",
             "The record states no file-level licences; all five archives and the CAMx table are marked provenance_review_required in the Stage 1 manifest."]
    notes += [
        "Roads come from OpenStreetMap (ODbL, attribution and share-alike for derived databases). Distributing roads.parquet or the road-density grid is subject to ODbL; whether the record's CC BY 4.0 satisfies this is NOT established: review before redistribution.",
        "Population: the slide deck names LandScan (Oak Ridge National Laboratory) as the source; its terms are not documented in the record and were not checked.",
        "The KML context layers appear to be hand-mapped (the deck mentions Google Earth scans); the terms of any underlying imagery are unknown.",
        "CAMx output and the emissions inventory are the record author's own products (CC BY 4.0), but their inputs (activity data, population, OSM roads) carry their own terms.",
        "The administrative polygon's origin is not stated.",
    ]
    return notes


def _dump(path: Path, obj, allow: bool) -> str:
    return io.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n", path, allow)


def run(*, zenodo_manifest: Path = ZENODO_MANIFEST, interim_dir: Path | None = None, processed_dir: Path | None = None,
        manifest_path: Path = PROCESSING_MANIFEST, spec: gg.GridSpec | None = None, allow_overwrite: bool = False, out=print) -> int:
    spec = spec or gg.GridSpec()
    interim = interim_dir or REPO_ROOT / "data" / "interim" / "gis"
    processed = processed_dir or REPO_ROOT / "data" / "processed" / "gis"
    ow = allow_overwrite
    zen = mf.load_manifest(zenodo_manifest)
    if zen is None:
        out(f"ERROR: Zenodo manifest not found: {zenodo_manifest}")
        return 2
    previous = mf.load_manifest(manifest_path) or {}

    out("Verifying and extracting archives ...")
    extraction = gx.extract_archives(zen, REPO_ROOT, interim / "extracted")
    prev = {(a["archive"], f["filename"]): f for a in previous.get("extraction", []) for f in a["files"]}
    now = mf.utc_now()
    for a in extraction:
        for f in a["files"]:
            old = prev.get((a["archive"], f["filename"]))
            f["extracted_at_utc"] = old["extracted_at_utc"] if old and old.get("sha256") == f["sha256"] else now
            f["local_path"] = io.rel(Path(f["local_path"]))
        a["extracted_to"] = io.rel(Path(a["extracted_to"]))
    camx_e = gx.manifest_entry(zen, gx.CAMX_FILE)
    camx_path = Path(camx_e["local_path"]) if Path(camx_e["local_path"]).is_absolute() else REPO_ROOT / camx_e["local_path"]
    camx_calc = ex.verify_archive(camx_path, camx_e["calculated_checksums"]["sha256"], camx_e["calculated_checksums"]["md5"])
    E = interim / "extracted"
    st: dict = {"processing_version": PROCESSING_VERSION, "extraction": extraction,
                "camx_file": {"filename": gx.CAMX_FILE, "size_bytes": camx_e["size_bytes"], "sha256": camx_calc["sha256"], "md5": camx_calc["md5"], "local_path": io.rel(camx_path)},
                "crs": {}}

    out("Grid, population, CAMx ...")
    pop_dir = E / "gis_bishkek-grids-pop"
    grid, st["grid"] = gg.load_grid(pop_dir / "grids_bishkek.shp", spec, pop_dir / "grids_bishkek.kml")
    st["crs"]["grids_bishkek.shp"] = st["grid"]["crs"]
    pop, st["population"] = gg.parse_population(pop_dir / "bishkek_pop_allyears.csv", grid, spec)
    camx, st["camx"] = gg.parse_camx(camx_path, grid, spec)

    out("Emissions ...")
    em_dir = E / "bishkek_gridded_emissions_2018"
    em_area, sectors, rep_area, tables = gg.read_emissions_area(em_dir, grid, spec)
    em_pts, rep_pts = gg.read_emissions_points(em_dir, grid, tables, spec)
    sg = sectors.groupby("sector").agg(files=("file", "size"), pm25=("pm25_t_yr", "sum")).sort_values("pm25", ascending=False)
    rep_area["sector_table"] = sectors.to_dict("records")
    st["emissions"] = {"area": rep_area, "points": rep_pts,
                       "sector_groups": [{"sector": s, "label": gg.SECTOR_META.get(s, ("(unrecognised)", ""))[0], "category": gg.SECTOR_META.get(s, ("", "unassigned"))[1],
                                          "files": int(r.files), "pm25": float(r.pm25), "share": float(100 * r.pm25 / sg["pm25"].sum())} for s, r in sg.iterrows()]}

    out("Roads ...")
    rd = E / "gis_bishkek_roads"
    master_raw = gpd.read_file(rd / "grids_bishkek_roads.shp")
    prov = lambda g: g.set_crs(gv.WGS84, allow_override=True)  # noqa: E731  (provisional, only for the consistency checks below)
    split_reports = [gg.check_cell_split_layer(prov(gpd.read_file(rd / f"grids_bishkek_{n}.shp")), prov(master_raw), grid, cl, f"grids_bishkek_{n}.shp")
                     for n, cl in (("primary", gg.ROAD_GROUPS["primary"]), ("secondary", gg.ROAD_GROUPS["secondary"]))]
    ev = [f"{r['layer']}: {r['segments_inside_their_Maille_cell']:,} of {r['segments']:,} segments lie inside the grid cell named by their Maille attribute when read as WGS84 degrees"
          for r in split_reports if r["segments_inside_their_Maille_cell"] == r["segments"]]
    ev.append("coordinates are plausible longitude/latitude values inside the study area")
    assess = gv.assess_crs(master_raw, "grids_bishkek_roads.shp", evidence=ev)
    st["crs"]["grids_bishkek_roads.shp (and primary/secondary)"] = assess
    roads = gv.to_wgs84(master_raw, assess)
    roads, geo_roads = gv.validate_geometries(roads)
    roads["road_group"] = gg.classify_road(roads["fclass"])
    dens, rep_dens = gg.road_density(roads, grid)
    dens, rep_cmp = gg.compare_source_density(dens, rd / "roads_density_byclass_bishkek.csv")
    st["roads"] = {"density": rep_dens, "source_comparison": rep_cmp, "layers": split_reports, "geometry": geo_roads,
                   "osm_id_duplicates": gv.duplicate_ids(roads, "osm_id")}

    out("Administrative boundary and context layers ...")
    admin, st["admin"] = gg.read_admin(E / "gis_bishkek_adm0" / "bishkek_adm2.shp", grid)
    st["crs"]["bishkek_adm2.shp"] = st["admin"]["crs"]
    ctx, rep_layers = gg.read_context_layers(E / "gis_bishkek_multiplelayers")
    ctx_grid, rep_cg = gg.context_grid(ctx, grid)
    for k, v in rep_layers.items():
        st["crs"][f"{k}.kml"] = v["crs"]
    st["context"] = {"layers": rep_layers, "grid": rep_cg}
    kgz = gg.kgz_fraction(admin, grid)
    st["admin"]["cells_fully_inside"] = int((kgz["frac_in_kgz_polygons"] >= 0.999).sum())
    st["admin"]["cells_partly_inside"] = int(((kgz["frac_in_kgz_polygons"] > 0.001) & (kgz["frac_in_kgz_polygons"] < 0.999)).sum())
    st["admin"]["cells_outside"] = int((kgz["frac_in_kgz_polygons"] <= 0.001).sum())

    out("Integration ...")
    pop_year = st["population"]["latest_year"]
    integrated, st["integration"] = gg.integrate(grid, pop, dens, camx, em_area, em_pts, ctx_grid, kgz, pop_year)
    outside = integrated["frac_in_kgz_polygons"] <= 0.001
    share = lambda col: float(100 * integrated.loc[outside, col].sum() / integrated[col].sum()) if integrated[col].sum() else 0.0  # noqa: E731
    st["admin"]["share_of_totals_in_cells_outside_kgz_pct"] = {"population": share(f"pop_{pop_year}"), "pm25_emissions": share("emis_pm25_total_t_yr"), "road_length": share("road_length_km_total")}
    dictionary = gr.data_dictionary([c for c in integrated.columns], pop_year)

    meta_common = {"processing_version": PROCESSING_VERSION, "source": "Zenodo record 12720883", "output_crs": gv.WGS84}
    outputs, P, V, R = [], processed, interim / "validated", interim / "reports"

    def add(path, fmt, role, rows, status):
        outputs.append(io.output_record(path, fmt, role, rows, status))

    def meta(**kw):
        return {**meta_common, **kw}

    p = P / "population_grid.parquet"
    add(p, "parquet", f"population per cell, annual {pop.columns[5][4:]}-{pop_year} (persons/cell, estimate)", len(pop),
        io.write_parquet(pop, p, meta(vintage=f"2000-{pop_year}", data_kind="gridded population estimate", source_crs="assumed EPSG:4326 (grid undeclared)"), ow))
    p = P / "roads.parquet"
    rcols = [c for c in ["osm_id", "fclass", "road_group", "name", "ref", "oneway", "maxspeed", "layer", "bridge", "tunnel", "code", "geom_status", "geom_invalid_reason", "geometry"] if c in roads.columns]
    add(p, "geoparquet", "all roads with OSM classes and our group", len(roads),
        io.write_geoparquet(roads[rcols], p, meta(source_crs=assess["status"], data_kind="static geographic (OpenStreetMap-derived)", vintage="not stated", licence_note="ODbL review required"), ow))
    p = P / "road_density_grid.parquet"
    add(p, "parquet", "road length and density per cell (not traffic)", len(dens),
        io.write_parquet(dens, p, meta(data_kind="static geographic", definition="km of road per cell and km/km2; NOT traffic volume"), ow))
    p = P / "administrative_boundaries.geojson"
    add(p, "geojson", "Kyrgyzstan polygons clipped to the airshed (country level)", len(admin), io.write_geojson(admin, p, ow))
    p = P / "context_layers.geojson"
    add(p, "geojson", "KML context layers (boilers, kilns, industrial areas, landfill, quarries, water, main roads, 2 unverified polygons)", len(ctx), io.write_geojson(ctx, p, ow))
    p = P / "camx_pm25_grid.parquet"
    add(p, "parquet", "MODELLED monthly and annual PM2.5 (ug/m3)", len(camx), io.write_parquet(camx, p, meta(data_kind="MODELLED", unit="ug/m3", vintage="year not stated (figures 2018)"), ow))
    p = P / "emissions_area_grid.parquet"
    add(p, "parquet", "2018 inventory per cell: PM2.5 by sector file and group, PM10, NO, NO2, CO, SO2 (t/cell/yr); includes point-source sectors", len(em_area),
        io.write_parquet(em_area, p, meta(data_kind="MODELLED inventory", vintage="2018", pm25_formula="FRPM+PEC+POA", units="t/cell/yr"), ow))
    p = P / "emissions_point_sources.parquet"
    add(p, "geoparquet", "point sources (subset of the area totals; do not add)", len(em_pts),
        io.write_geoparquet(em_pts, p, meta(data_kind="MODELLED inventory", vintage="2018", warning="subset of emissions_area_grid; do not add"), ow))
    p = P / "airwatch_spatial_grid.parquet"
    add(p, "geoparquet", "integrated grid: one row per cell", len(integrated),
        io.write_geoparquet(integrated, p, meta(population_year=pop_year, data_dictionary=dictionary, note="vintages differ; CAMx and emissions are related model products"), ow))
    sp = V / "emissions_sector_summary.csv"
    add(sp, "csv", "per-file emissions totals by pollutant (t/yr)", len(sectors), io.guarded_write(sp, lambda t: sectors.to_csv(t, index=False, lineterminator="\n"), ow))

    st["decisions"], st["limitations"], st["licences"] = DECISIONS, LIMITATIONS, licence_notes(zen, extraction)
    p = V / "gis_validation_checks.json"
    add(p, "json", "all validation checks (machine-readable)", None, _dump(p, {k: v for k, v in st.items() if k != "extraction"}, ow))
    p = R / "gis_validation_report.md"
    add(p, "markdown", "consolidated validation report", None, io.write_text(gr.render(st, DECISIONS, LIMITATIONS, st["licences"]), p, ow))

    manifest = {
        "schema_version": 1, "stage": "gis_processing", "generated_at_utc": mf.utc_now(),
        "processing": {"script": SCRIPT, "version": PROCESSING_VERSION, "python": platform.python_version(), "geopandas": gpd.__version__, "shapely": shapely.__version__,
                       "pyogrio": pyogrio.__version__, "pyproj": pyproj.__version__, "pandas": pd.__version__, "pyarrow": pyarrow.__version__, "extraction_tool": ex.tar_version()},
        "source": {"zenodo_record_id": zen["record"]["record_id"], "doi": zen["record"]["doi"], "zenodo_manifest": io.rel(zenodo_manifest),
                   "archives": [{"filename": a["archive"], "size_bytes": a["size_bytes"], "zenodo_checksum": a["zenodo_checksum"], "md5": a["md5"], "sha256": a["sha256"],
                                 "verified_against_zenodo_manifest": True, "provenance_review_required": a["provenance_review_required"]} for a in extraction]
                   + [{"filename": gx.CAMX_FILE, "size_bytes": camx_e["size_bytes"], "zenodo_checksum": camx_e["zenodo_checksum"], "md5": camx_calc["md5"], "sha256": camx_calc["sha256"],
                       "verified_against_zenodo_manifest": True, "provenance_review_required": camx_e["provenance"]["review_required"]}]},
        "extraction": [{"archive": a["archive"], "output_dir": a["extracted_to"], "files": a["files"]} for a in extraction],
        "crs": {k: {kk: vv for kk, vv in v.items() if kk != "bounds"} for k, v in st["crs"].items()},
        "output_crs": gv.WGS84, "metric_crs_for_lengths_and_areas": gv.UTM43N,
        "spatial_resolution": {"grid_cells": f"{spec.nx} x {spec.ny}", "cell_size_deg": spec.step, "cell_size_km_approx": st["grid"]["approx_cell_size_km"],
                               "extent_deg": [spec.lon0, spec.lat0, spec.lon0 + spec.nx * spec.step, spec.lat0 + spec.ny * spec.step]},
        "data_vintage": {"population": f"2000-{pop_year}", "emissions": "2018", "camx": "year not stated (figures named 2018)", "roads": "OSM extract date not stated", "context_layers": "not stated"},
        "data_kinds": {"population": "gridded estimate", "roads": "static geographic", "camx": "MODELLED", "emissions": "MODELLED inventory", "context_layers": "static geographic (mapped)"},
        "integration_methodology": DECISIONS,
        "grid_columns": dictionary,
        "validation_summary": {"grid_checks_passed": st["grid"]["all_checks_passed"], "population_years": st["population"]["years"], "roads": st["roads"]["density"]["segments_input"],
                               "emissions_pm25_total_t_yr": rep_area["pm25_total_t_yr"], "point_sources_reconcile": rep_pts.get("all_reconcile"),
                               "camx_annual_domain_mean": st["camx"]["annual_mean"]["domain_mean"]},
        "outputs": outputs,
        "licence": {"record_level_licence": zen["record"]["licence"], "attribution_required": True, "notes": st["licences"]},
        "known_limitations": LIMITATIONS,
    }
    mf.write_manifest_atomic(manifest_path, manifest)
    out(f"Processing manifest written: {io.rel(manifest_path)}")
    for o in outputs:
        out(f"  {o['size_bytes']:>11,}  {o['path']}  [{o['write_status']}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--allow-overwrite", action="store_true", help="allow replacing existing outputs whose content would change")
    args = a.parse_args(argv)
    try:
        return run(allow_overwrite=args.allow_overwrite)
    except (ex.ExtractionError, gv.GisError, io.OutputExistsError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
