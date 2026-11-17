"""Data dictionary and the consolidated Markdown validation report for Stage 4."""

from __future__ import annotations

import re

from pipeline.embassy_report import _table
from pipeline.gis_grid import ROAD_GROUPS


def _f(x, n=2):
    return "n/a" if x is None else f"{x:,.{n}f}"


def data_dictionary(columns: list[str], pop_year: int) -> list[dict]:
    """One entry per column of airwatch_spatial_grid.parquet: meaning, unit, data kind, source, vintage."""
    out = []
    for c in columns:
        e = {"column": c}
        if c in ("cell_id", "ix", "iy"):
            e.update(definition={"cell_id": "grid cell id = (iy-1)*50 + ix", "ix": "column index 1-50 (west to east)", "iy": "row index 1-40 (south to north)"}[c],
                     unit="", data_kind="key", source="Zenodo grid", vintage="")
        elif c in ("lon_c", "lat_c"):
            e.update(definition="cell centre", unit="degrees (WGS84)", data_kind="key", source="Zenodo grid", vintage="")
        elif c == "area_km2":
            e.update(definition="cell area computed in UTM 43N (EPSG:32643); the source area_cell attribute differs by < 0.002 km2", unit="km2", data_kind="static geometry", source="Zenodo grid", vintage="")
        elif c == "geometry":
            e.update(definition="cell polygon", unit="WGS84", data_kind="static geometry", source="Zenodo grid", vintage="")
        elif c.startswith("pop_"):
            e.update(definition=f"population {c[4:]}: gridded population estimate, persons per cell (unit assumed; none stated)", unit="persons/cell",
                     data_kind="gridded population ESTIMATE (not a census count)", source="Zenodo bishkek_pop_allyears.csv (deck names LandScan; unverified)", vintage=c[4:])
        elif c.startswith("road_length_km_"):
            e.update(definition=f"length of {c[15:]} roads inside the cell (OSM classes grouped; see report)", unit="km/cell", data_kind="static geographic (OpenStreetMap-derived); NOT traffic", source="Zenodo roads shapefile", vintage="OSM extract date not stated")
        elif c.startswith("road_density_km_per_km2_"):
            e.update(definition=f"road length / cell area, {c[24:]}", unit="km/km2", data_kind="static geographic (OpenStreetMap-derived); NOT traffic volume", source="Zenodo roads shapefile", vintage="OSM extract date not stated")
        elif c.startswith("camx_pm25_"):
            e.update(definition=("annual mean = unweighted mean of the 12 monthly means" if c.endswith("annual_mean") else f"modelled monthly mean PM2.5, {c[10:].upper()}"),
                     unit="ug/m3", data_kind="MODELLED (WRF-CAMx); not a measurement", source="Zenodo bishkek_camx_pm25_monthlyavg.csv", vintage="year not stated in the file (figures named 2018)",
                     note="Driven by the same 2018 emissions inventory as emis_*: not independent evidence of it.")
        elif c.startswith("emis_pm25_") or c.startswith("emis_pm10") or re.match(r"emis_(no|no2|co|so2)_total", c):
            e.update(definition=f"2018 bottom-up emissions inventory, {c}; PM2.5 = FRPM + PEC + POA", unit="t/cell/yr" if "per_km2" not in c else "t/km2/yr",
                     data_kind="MODELLED inventory (not measured)", source="Zenodo bishkek_gridded_emissions_2018.rar", vintage="2018",
                     note="The .emi files already include the point sources; do not add emis_point_sources_*.")
        elif c in ("n_point_sources", "emis_point_sources_pm25_t_yr"):
            e.update(definition="point sources (brick kilns, heavy industry) located in the cell; their PM2.5 is a SUBSET of the cell's emis_pm25_total_t_yr", unit="count / t/yr",
                     data_kind="MODELLED inventory", source="Zenodo .ptemi files", vintage="2018", note="Do not add to emis_pm25_total_t_yr (double counting).")
        elif c.startswith(("n_bte", "n_brick")):
            e.update(definition="number of mapped points of this layer inside the cell", unit="count", data_kind="static geographic (mapped features)", source="Zenodo KML layer", vintage="not stated")
        elif c.startswith("frac_in_kgz"):
            e.update(definition="share of the cell inside the Kyrgyzstan polygons (cells at the northern edge may partly lie outside)", unit="fraction", data_kind="static geographic", source="Zenodo bishkek_adm2.shp", vintage="not stated")
        elif c.startswith("frac_"):
            e.update(definition=f"share of the cell covered by {c[5:]}", unit="fraction", data_kind="static geographic (mapped polygons)", source="Zenodo KML layer", vintage="not stated",
                     note="Meaning of this layer is unverified." if "unverified" in c else "")
        else:
            e.update(definition="(undocumented column)", unit="", data_kind="", source="", vintage="")
        out.append(e)
    return out


def render(st: dict, decisions: list[str], limitations: list[str], licences: list[str]) -> str:
    L = ["# GIS and spatial datasets: validation report (Stage 4)\n",
         f"Processing version `{st['processing_version']}`. Source: Zenodo record 12720883 (CC BY 4.0 at record level; see section 14). "
         "All outputs are in WGS84 (EPSG:4326). Population, roads, CAMx and emissions are **not measurements**.\n"]

    L.append("## 1. Source datasets and file inventory\n")
    rows = []
    for a in st["extraction"]:
        roles = {}
        for f in a["files"]:
            roles[f["role"]] = roles.get(f["role"], 0) + 1
        rows.append([a["archive"], f"{a['size_bytes']:,}", len(a["files"]), "; ".join(f"{n} {r}" for r, n in roles.items())])
    rows.append([st["camx_file"]["filename"], f"{st['camx_file']['size_bytes']:,}", 1, "CAMx monthly PM2.5 table (not an archive)"])
    L.append(_table(["file", "bytes", "members", "contents"], rows))
    L.append("\nNot processed (reported only): 21 PNG/GIF population figures, 4 CAMx temporal-profile CSVs, the duplicate `.bna` boundary file, and the empty `Unknown` layer inside each KML.\n")

    L.append("## 2. Extraction results\n")
    L.append(_table(["archive", "files extracted", "bytes", "checksum verified vs Zenodo manifest"],
                    [[a["archive"], len(a["files"]), f"{sum(f['size_bytes'] for f in a['files']):,}", "yes"] for a in st["extraction"]]))
    L.append("\nEach archive is verified (MD5 and SHA-256) before extraction, member names are checked for unsafe paths, existing different files are never overwritten, and the original archives are untouched.\n")

    L.append("## 3. Coordinate systems\n")
    crs_rows = []
    for name, c in st["crs"].items():
        crs_rows.append([name, c["declared_crs"] or "**none declared**", c["status"], "; ".join(c["evidence"]) or "—"])
    L.append(_table(["dataset", "declared CRS", "decision", "evidence"], crs_rows))
    L.append("\nWhere no CRS is declared (the grid, population and road shapefiles have no `.prj`), WGS84 is an **assumption** supported by the evidence shown; it is recorded in the manifest. "
             "Lengths and areas are computed in UTM zone 43N (EPSG:32643). The road `length` attribute is in degrees and is not used.\n")

    g = st["grid"]
    L.append("## 4. Spatial coverage and grid resolution\n")
    c = g["checks"]
    L.append(f"Reference grid: **{c['n_features']:,} cells** (expected {c['expected']:,}), 50 × 40 cells of {g['resolution_deg']}° (≈ {g['approx_cell_size_km'][0]:.2f} × {g['approx_cell_size_km'][1]:.2f} km), "
             f"covering 74.35–74.85 °E, 42.70–43.10 °N. Cell area {_f(c['area_km2_range'][0], 3)}–{_f(c['area_km2_range'][1], 3)} km² (computed); the source `area_cell` attribute differs by at most "
             f"{c['area_cell_src_vs_computed_max_abs_diff_km2']:.4f} km². Checks passed: {g['all_checks_passed']} (unique ids, id formula, lattice, polygon bounds).\n")
    adm = st["admin"]
    L.append(f"Administrative file: {adm['features']} polygons, all labelled {adm['distinct_codes']} / {adm['distinct_names']}. They cover {adm['share_of_grid_box_covered_pct']:.1f}% of the grid box "
             f"({adm['union_area_km2']:,.0f} of {adm['grid_box_km2']:,.0f} km²): {adm.get('cells_fully_inside', 'n/a')} cells lie fully inside, {adm.get('cells_partly_inside', 'n/a')} partly inside and "
             f"{adm.get('cells_outside', 'n/a')} outside (see `frac_in_kgz_polygons`). The cells entirely outside hold "
             + (", ".join(f"{v:.1f}% of {k.replace('_', ' ')}" for k, v in adm.get("share_of_totals_in_cells_outside_kgz_pct", {}).items()) or "n/a")
             + "; the source datasets contain values for them regardless of the border. " + adm["level_note"] + "\n")

    L.append("## 5. Feature counts\n")
    ct = st["context"]["layers"]
    rr = st["roads"]["density"]
    rows = [["grid cells", c["n_features"], "polygons"], ["population rows", st["population"]["rows_in_csv"], "per cell, 20 years"],
            ["CAMx rows", st["camx"]["rows"], "per cell, 12 months"], ["roads (all classes)", rr["segments_input"], "lines"],
            ["administrative polygons", adm["features"], "polygons"], ["emissions area files", st["emissions"]["area"]["area_files"], "52 sector files × 2,000 cells"],
            ["point-source rows", st["emissions"]["points"]["rows"], "points (5 files)"]]
    rows += [[f"context: {k}", v["features"], v["geometry_kind"]] for k, v in ct.items()]
    L.append(_table(["dataset", "features/rows", "kind"], rows))

    L.append("\n## 6. Geometry validity\n")
    gv_rows = [["grid", g["geometry"]], ["roads", st["roads"]["geometry"]], ["administrative", adm["geometry"]], ["point sources", st["emissions"]["points"].get("geometry", {})]]
    gv_rows += [[f"context {k}", v["geometry"]] for k, v in ct.items()]
    L.append(_table(["layer", "features", "null", "empty", "invalid", "duplicate geometries"],
                    [[n, d.get("features", 0), d.get("null", 0), d.get("empty", 0), d.get("invalid", 0), d.get("duplicate_geometries", 0)] for n, d in gv_rows]))
    L.append("\nNo feature was removed or repaired in the stored data. (Repairs, if any were needed, are used only transiently for area calculations.)\n")

    L.append("## 7. Missing and duplicate records\n")
    p, cm, em = st["population"], st["camx"], st["emissions"]["area"]
    L.append(f"- Population: {p['grid_cells_without_population_row']} grid cells without a row, {p['csv_rows_not_on_grid']} rows off the grid, {p['duplicate_cells']} duplicate cells, "
             f"{p['midpoints_off_lattice']} midpoints off the lattice; NaN/negative values in any year: "
             f"{sum(v['nan'] + v['negative'] for v in p['per_year'].values())}.\n"
             f"- CAMx: {cm['unique_cells']:,} unique cells of {cm['expected_rows']:,}; {cm['grid_cells_without_value']} grid cells without a value; NaN {cm['nan']}, negative {cm['negative']}.\n"
             f"- Emissions: problems per check {em['problems']}.\n"
             f"- Roads: osm_id unique in the master layer: {st['roads']['osm_id_duplicates'] == 0} (duplicates {st['roads']['osm_id_duplicates']}); road names are unusable (see section 14).\n"
             f"- Point sources with missing coordinates: {st['emissions']['points']['missing_coordinates']}. "
             f"{st['emissions']['points']['geometry']['duplicate_geometries']} point rows share exactly the same location as another row: expected, because each brick kiln appears once per fuel file "
             "(coal, oil, wood, other) and several heavy-industry sources sit on one site. They are separate records and are kept.\n")

    L.append("## 8. Grid resolution and integration unit\n")
    L.append("Everything is aggregated or resampled onto the 0.01° grid, which is also the native resolution of the population, CAMx and emissions tables. Roads and context layers are vector data "
             "and are clipped to cells. The grid does not make any source more accurate than it is.\n")

    L.append("## 9. Population data year\n")
    L.append(f"Years available: {p['years'][0]}–{p['years'][-1]} (annual); the latest, **{p['latest_year']}**, is used in the integrated grid (`pop_{p['latest_year']}`); all years are kept in `population_grid.parquet`. "
             f"{p['unit_note']} {p['source_note']}\n")
    L.append(_table(["year", "sum", "min", "max", "zero cells"], [[y, f"{v['sum']:,.0f}", _f(v["min"]), _f(v["max"]), v["zero_cells"]] for y, v in p["per_year"].items() if y in (p["years"][0], 2005, 2010, 2015, p["years"][-1])]))

    L.append("\n## 10. Road classification and density\n")
    rd = rr
    L.append(f"{rd['segments_input']:,} OSM-derived segments ({rd['total_length_km_unclipped_utm']:,.0f} km). Classes are grouped as below; density = road length clipped to the cell ÷ cell area. "
             "**Road density is not traffic volume.**\n")
    L.append(_table(["group", "OSM classes", "features", "length km", "mean km/km²", "max km/km²"],
                    [[k, ", ".join(ROAD_GROUPS.get(k, ["(not in any listed class)"])), v["features"], _f(v["length_km"], 1), _f(v["mean_density_km_per_km2"], 3), _f(v["max_density_km_per_km2"], 2)]
                     for k, v in rd["by_group"].items()]))
    L.append(f"\nCells containing roads: {rd['cells_with_roads']:,} of {c['n_features']:,}. Unlisted OSM classes: {rd['unlisted_fclass'] or 'none'}.\n")
    sc = st["roads"]["source_comparison"]
    L.append("Comparison with the source density CSV (which holds **km of road per cell**, not per km²):\n")
    L.append(_table(["source column", "correlation with our length", "mean source km", "mean ours km", "max |diff| km"],
                    [[k, _f(v["corr"], 5), _f(v["mean_source"], 4), _f(v["mean_ours"], 4), _f(v["max_abs_diff_km"], 3)] for k, v in sc.items() if isinstance(v, dict)]))
    L.append(f"\n{sc['interpretation']}\n")
    for lay in st["roads"]["layers"]:
        L.append(f"- `{lay['layer']}`: {lay['segments']:,} segments, osm_ids identical to the master layer's classes: {lay['osm_ids_equal_master_classes']}; "
                 f"{lay['segments_inside_their_Maille_cell']:,} lie inside the cell named by `Maille`. {lay['length_attribute_unit_note']}.")
    L.append("")

    L.append("## 11. CAMx modelled PM2.5\n")
    L.append(f"**{cm['data_kind']}**; unit {cm['unit']}. {cm['year_note']} Grid check: {cm['rows']:,} rows, {cm['unique_cells']:,} unique cells, ix {cm['ix_range']}, iy {cm['iy_range']}, 12 monthly values.\n")
    L.append(_table(["month", "domain mean", "cell min", "cell max"], [[m.upper(), _f(cm["domain_monthly_mean"][m], 1), _f(cm["monthly_min_max"][m][0], 1), _f(cm["monthly_min_max"][m][1], 1)] for m in cm["domain_monthly_mean"]]))
    a = cm["annual_mean"]
    L.append(f"\nAnnual mean ({cm['annual_mean_definition']}): domain mean {a['domain_mean']:.1f}, cell min {a['min']:.1f}, median {a['median']:.1f}, max {a['max']:.1f} µg/m³ (cell {a['cell_of_max']}).\n"
             "CAMx values are model output and must never be shown as current or measured concentrations. CAMx and the emissions inventory are **related** (the inventory is a model input), so agreement between them is not independent confirmation.\n")

    L.append("## 12. Emissions by sector and pollutant\n")
    L.append(f"**{em['data_kind']}**, {em['vintage']}, units {em['units']}. PM2.5 = {em['pm25_formula']}. Total PM2.5 {em['pm25_total_t_yr']:,.1f} t/yr "
             f"(the paper reports about {em['paper_reference_total_t_yr']:,} t/yr); PM10 {em['pm10_total_t_yr']:,.1f} t/yr.\n")
    sec = st["emissions"]["sector_groups"]
    L.append(_table(["sector (file prefix)", "label (inferred)", "paper category (inferred)", "files", "PM2.5 t/yr", "share %"],
                    [[s["sector"], s["label"], s["category"], s["files"], f"{s['pm25']:,.1f}", f"{s['share']:.1f}"] for s in sec]))
    L.append("\nSector labels are inferred from file names and the paper's Table 2 caption; they are not defined in the Zenodo record. Species totals (t/yr): "
             + ", ".join(f"{k.upper()} {v:,.0f}" for k, v in em["species_totals_t_yr"].items()) + ".\n")
    pt = st["emissions"]["points"]
    L.append(f"Point sources: {pt['rows']} rows in {pt['files']} files (brick kilns and heavy industry). Reconciliation with the `.emi` files:\n")
    L.append(_table(["file", "rows", "PM2.5 points t/yr", "PM2.5 area file t/yr", "max |diff|", "reconciles"],
                    [[r["file"], r["rows"], _f(r["pm25_points_t_yr"], 2), _f(r["pm25_area_file_t_yr"], 2), f"{r['max_abs_diff_t_yr']:.2g}", r["reconciles_with_area_file"]] for r in pt["reconciliation"]]))
    L.append(f"\n**{pt['double_counting_warning']}** {pt['stack_parameter_note']} {pt['unspecified_columns_note']}\n")

    L.append("## 13. Spatial integration methodology\n")
    L += [f"- {d}" for d in decisions]
    ig = st["integration"]
    L.append(f"\nResult: `airwatch_spatial_grid.parquet`, {ig['rows']:,} rows × {ig['columns']} columns, one row per cell: {ig['one_row_per_cell']}. Columns with missing values: {ig['missing_values_per_column'] or 'none'}.\n")
    cx = st["context"]["grid"]
    L.append(f"Context counts: {cx['boilers_counted']} boilers in {cx['cells_with_boilers']} cells; {cx['kilns_counted']} brick kilns in {cx['cells_with_brick_kilns']} cells. "
             "Layers whose meaning is unverified are carried under names ending `_unverified` and are not interpreted. Layer attributes are almost empty (only the boilers have names), "
             "so these layers are kept as identifiable geometry with a layer label, flagged by `layer_meaning_status`.\n")

    L.append("## 14. Known limitations and licensing issues\n")
    L += [f"- {x}" for x in limitations]
    L.append("\n### Licensing and attribution\n")
    L += [f"- {x}" for x in licences]
    return "\n".join(L) + "\n"
