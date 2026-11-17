import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from pipeline import gis_grid as gg
from pipeline import gis_report as gr
from pipeline import gis_validate as gv
from pipeline.tests import gis_helpers as H

SPEC = H.SPEC


@pytest.fixture
def grid(tmp_path):
    H.write_grid(tmp_path / "g", SPEC)
    g, _ = gg.load_grid(tmp_path / "g" / "grids_bishkek.shp", SPEC, tmp_path / "g" / "grids_bishkek.kml")
    return g


# ------------------------------------------------------------------ reference grid
def test_load_grid_checks_crs_assumption_and_kml_evidence(tmp_path):
    H.write_grid(tmp_path, SPEC)
    g, rep = gg.load_grid(tmp_path / "grids_bishkek.shp", SPEC, tmp_path / "grids_bishkek.kml")
    assert rep["all_checks_passed"] and len(g) == 6 and g["cell_id"].tolist() == [1, 2, 3, 4, 5, 6]
    assert g.crs.to_epsg() == 4326 and rep["crs"]["status"] == "assumed_wgs84_undeclared"
    assert any("KML" in e for e in rep["crs"]["evidence"]) and rep["checks"]["kml_centroids_matched"] == 6
    assert g["area_km2"].between(0.89, 0.92).all() and rep["checks"]["cell_id_formula_ok"]
    assert set(g["geom_status"]) == {"ok"} and rep["resolution_deg"] == 0.01


def test_load_grid_without_kml_still_assumes_wgs84_from_the_lattice(tmp_path):
    H.write_grid(tmp_path, SPEC, kml=False)
    _, rep = gg.load_grid(tmp_path / "grids_bishkek.shp", SPEC)
    assert rep["crs"]["status"] == "assumed_wgs84_undeclared" and len(rep["crs"]["evidence"]) == 1


def test_load_grid_flags_wrong_cell_count_and_bad_lattice(tmp_path):
    g = H.grid_source_frame(SPEC, drop_cell=3)
    H._quiet_write(g, tmp_path / "grids_bishkek.shp")
    _, rep = gg.load_grid(tmp_path / "grids_bishkek.shp", SPEC)
    assert rep["checks"]["count_ok"] is False and rep["all_checks_passed"] is False
    g2 = H.grid_source_frame(SPEC)
    g2["Y"] = "42.800"                                                         # centres no longer on the lattice
    H._quiet_write(g2, tmp_path / "b.shp")
    _, rep2 = gg.load_grid(tmp_path / "b.shp", SPEC)
    assert rep2["checks"]["centres_match_lattice"] is False and rep2["all_checks_passed"] is False


def test_load_grid_needs_the_expected_columns(tmp_path):
    H._quiet_write(gpd.GeoDataFrame({"x": [1]}, geometry=[box(74.35, 42.7, 74.36, 42.71)]), tmp_path / "bad.shp")
    with pytest.raises(gv.GisError, match="lacks columns"):
        gg.load_grid(tmp_path / "bad.shp", SPEC)


# ------------------------------------------------------------------ population
def test_population_join_years_and_aggregation(tmp_path, grid):
    H.write_population(tmp_path / "p.csv", SPEC)
    pop, rep = gg.parse_population(tmp_path / "p.csv", grid, SPEC)
    assert rep["years"] == [2018, 2019] and rep["latest_year"] == 2019 and rep["rows_in_csv"] == 6
    assert pop["pop_2019"].sum() == 2130 and pop["pop_2018"].sum() == 2100
    assert rep["per_year"][2019]["sum"] == 2130 and rep["per_year"][2018]["max"] == 600
    assert pop.set_index("cell_id").loc[5, "pop_2019"] == 505                    # joined to the right cell by its midpoint
    assert rep["grid_cells_without_population_row"] == 0 and rep["csv_rows_not_on_grid"] == 0 and rep["duplicate_cells"] == 0
    assert "persons per cell" in rep["unit_note"] and "unverified" in rep["source_note"].lower()


def test_population_missing_cell_is_nan_not_zero_and_reported(tmp_path, grid):
    H.write_population(tmp_path / "p.csv", SPEC, drop_cell=4)
    pop, rep = gg.parse_population(tmp_path / "p.csv", grid, SPEC)
    assert rep["grid_cells_without_population_row"] == 1 and np.isnan(pop.set_index("cell_id").loc[4, "pop_2019"])
    assert len(pop) == 6                                                           # the cell stays in the table


def test_population_off_grid_off_lattice_duplicates_and_negatives(tmp_path, grid):
    extra = [{"midlong": 74.9, "midlat": 43.2, "popgrid_bishkek_2018": 1, "popgrid_bishkek_2019": 1},          # outside the grid
             {"midlong": 74.358, "midlat": 42.705, "popgrid_bishkek_2018": 2, "popgrid_bishkek_2019": 2}]      # off-centre duplicate of cell 1
    H.write_population(tmp_path / "p.csv", SPEC, extra_rows=extra, negative=True)
    _, rep = gg.parse_population(tmp_path / "p.csv", grid, SPEC)
    assert rep["csv_rows_not_on_grid"] == 1 and rep["midpoints_off_lattice"] == 2 and rep["duplicate_cells"] == 1
    assert rep["per_year"][2018]["negative"] == 1


def test_population_needs_coordinates_and_year_columns(tmp_path, grid):
    pd.DataFrame({"a": [1]}).to_csv(tmp_path / "x.csv", index=False)
    with pytest.raises(gv.GisError, match="midlong"):
        gg.parse_population(tmp_path / "x.csv", grid, SPEC)
    pd.DataFrame({"midlong": [74.355], "midlat": [42.705]}).to_csv(tmp_path / "y.csv", index=False)
    with pytest.raises(gv.GisError, match="no popgrid"):
        gg.parse_population(tmp_path / "y.csv", grid, SPEC)


# ------------------------------------------------------------------ CAMx
def test_camx_grid_parsing_monthly_and_annual(tmp_path, grid):
    H.write_camx(tmp_path / "c.csv", SPEC)
    camx, rep = gg.parse_camx(tmp_path / "c.csv", grid, SPEC)
    assert rep["rows"] == 6 and rep["unique_cells"] == 6 and rep["ix_range"] == [1, 3] and rep["iy_range"] == [1, 2] and rep["months"] == 12
    c = camx.set_index("cell_id")
    assert c.loc[2, "camx_pm25_jan"] == 3.0 and c.loc[2, "camx_pm25_dec"] == 14.0
    assert c.loc[2, "camx_pm25_annual_mean"] == pytest.approx(2 + 6.5)             # unweighted mean of the 12 months
    assert rep["domain_monthly_mean"]["jan"] == pytest.approx(np.mean([1, 2, 3, 4, 5, 6]) + 1)
    assert rep["annual_mean"]["cell_of_max"] == [3, 2] and "MODELLED" in rep["data_kind"] and "not a measurement" in rep["data_kind"]
    assert rep["nan"] == 0 and rep["negative"] == 0 and rep["grid_cells_without_value"] == 0


def test_camx_missing_cell_and_negative_values_are_reported(tmp_path, grid):
    H.write_camx(tmp_path / "c.csv", SPEC, drop_cell=5, negative=True)
    camx, rep = gg.parse_camx(tmp_path / "c.csv", grid, SPEC)
    assert rep["grid_cells_without_value"] == 1 and np.isnan(camx.set_index("cell_id").loc[5, "camx_pm25_annual_mean"]) and rep["negative"] == 1
    pd.DataFrame({"ix": [1], "iy": [1], "JAN": [1.0]}).to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(gv.GisError, match="lacks columns"):
        gg.parse_camx(tmp_path / "bad.csv", grid, SPEC)


# ------------------------------------------------------------------ emissions
def test_pm25_is_frpm_plus_pec_plus_poa_and_sectors_are_preserved(tmp_path, grid):
    H.write_emissions(tmp_path, SPEC)
    wide, sec, rep, tables = gg.read_emissions_area(tmp_path, grid, SPEC)
    w = wide.set_index("cell_id")
    assert w.loc[1, "pm25_vehexh_cars_t_yr"] == 15 and w.loc[1, "pm10_total_t_yr"] == 19         # 10+2+3 (CRPM only in PM10)
    assert rep["pm25_total_t_yr"] == 58 and rep["pm10_total_t_yr"] == 68
    assert rep["pm25_by_sector_group_t_yr"] == {"indheavy": 30, "vehexh": 18, "brick": 10}
    assert w.loc[2, "pm25_vehexh_t_yr"] == 3 and w.loc[6, "pm25_brick_t_yr"] == 10 and w.loc[5, "pm25_indheavy_t_yr"] == 30
    assert rep["species_totals_t_yr"]["no"] == 162 and rep["species_totals_t_yr"]["so2"] == 9
    assert w.loc[1, "pm25_total_t_per_km2_yr"] == pytest.approx(15 / grid.set_index("cell_id").loc[1, "area_km2"])
    assert list(sec["sector"]) == ["brick", "indheavy", "vehexh"] and sec["paper_category_inferred"].tolist() == ["industry", "industry", "transport"]
    assert "inferred" in sec.columns[3] and rep["problems"] == {"row_count": [], "off_grid": [], "negative": [], "nan": [], "centre_mismatch": []}
    assert "FRPM + PEC + POA" in rep["pm25_formula"] and "not a measurement" in rep["data_kind"] and rep["vintage"] == "2018 base year"
    assert len(tables) == 3


def test_emi_parsing_lf_vs_crlf_and_problem_detection(tmp_path, grid):
    H.write_emi(tmp_path, "bishkek_2018_vehexh_cars.emi", {(1, 1): {"FRPM": 1}}, SPEC, crlf=False)
    assert len(gg.parse_emi(tmp_path / "bishkek_2018_vehexh_cars.emi")) == 6
    H.write_emi(tmp_path, "bishkek_2018_vehexh_cars.emi", {(1, 1): {"FRPM": -1}}, SPEC)
    _, _, rep, _ = gg.read_emissions_area(tmp_path, grid, SPEC)
    assert rep["problems"]["negative"] == ["bishkek_2018_vehexh_cars.emi"]
    H.write_emi(tmp_path, "bishkek_2018_vehexh_cars.emi", {}, SPEC, header='"ix,iy,lon,lat,FRPM"')
    with pytest.raises(gv.GisError, match="unexpected header"):
        gg.parse_emi(tmp_path / "bishkek_2018_vehexh_cars.emi")
    (tmp_path / "bishkek_2018_vehexh_cars.emi").write_text('"ix,iy,midlong,midlat,FRPM,CRPM,PEC,POA,NO,NO2,CO,SO2"\n1,1,74.355,42.705,1,1,1,1,1,1,1,1\n', encoding="utf-8")
    _, _, rep, _ = gg.read_emissions_area(tmp_path, grid, SPEC)
    assert rep["problems"]["row_count"] == ["bishkek_2018_vehexh_cars.emi"]             # short file reported, missing cells become 0 only in the sums
    with pytest.raises(gv.GisError, match="Unrecognised"):
        gg.split_name("something_else.emi")


def test_point_sources_reconcile_with_area_files_and_keep_stack_columns(tmp_path, grid):
    H.write_emissions(tmp_path, SPEC)
    _, _, _, tables = gg.read_emissions_area(tmp_path, grid, SPEC)
    pts, rep = gg.read_emissions_points(tmp_path, grid, tables, SPEC)
    assert len(pts) == 3 and rep["files"] == 2 and rep["missing_coordinates"] == 0 and rep["all_reconcile"] is True
    assert pts.crs.to_epsg() == 4326 and pts["lonlat_in_cell"].all() and (pts.geom_type == "Point").all()
    assert pts["pm25_t_yr"].tolist() == [6.0, 4.0, 30.0] and pts["unspecified_7"].sum() == 0       # FRPM + PEC + POA per row
    heavy = pts[pts["sector"] == "indheavy"].iloc[0]
    assert (heavy["stack_param_1"], heavy["stack_param_4"]) == (50, 400) and pts[pts["sector"] == "brick"]["stack_param_1"].isna().all()
    assert "NOT verified" in rep["stack_parameter_note"] and "must NOT be added" in rep["double_counting_warning"]
    assert {r["file"]: r["reconciles_with_area_file"] for r in rep["reconciliation"]} == {"bishkek_2018_brick_coal.ptemi": True, "bishkek_2018_indheavy_indheavy.ptemi": True}


def test_point_source_that_does_not_match_the_area_file_is_flagged(tmp_path, grid):
    H.write_emissions(tmp_path, SPEC, break_reconciliation=True)
    _, _, _, tables = gg.read_emissions_area(tmp_path, grid, SPEC)
    _, rep = gg.read_emissions_points(tmp_path, grid, tables, SPEC)
    assert rep["all_reconcile"] is False
    bad = next(r for r in rep["reconciliation"] if r["sector"] == "brick")
    assert bad["reconciles_with_area_file"] is False and bad["max_abs_diff_t_yr"] == pytest.approx(1.0)


def test_point_source_outside_its_cell_and_wrong_column_count(tmp_path, grid):
    H.write_ptemi(tmp_path, "bishkek_2018_brick_coal.ptemi", [H.pt_row(1, 1, {"FRPM": 1}, dx=0.02)])
    pts, _ = gg.read_emissions_points(tmp_path, grid, {}, SPEC)
    assert pts["lonlat_in_cell"].tolist() == [False]
    H.write_ptemi(tmp_path, "bishkek_2018_brick_oil.ptemi", [[1, 2, 3]])
    with pytest.raises(gv.GisError, match="expected 15 or 19"):
        gg.read_emissions_points(tmp_path, grid, {}, SPEC)


# ------------------------------------------------------------------ roads
def _roads(grid):
    r = H.roads_source_frame(SPEC).set_crs(4326)
    r, _ = gv.validate_geometries(r)
    r["road_group"] = gg.classify_road(r["fclass"])
    return r


def test_road_density_clips_lines_to_cells_and_keeps_classes(grid):
    roads = _roads(grid)
    dens, rep = gg.road_density(roads, grid)
    d = dens.set_index("cell_id")
    total_l1 = d.loc[[1, 2, 3], "road_length_km_primary"].sum()
    assert total_l1 == pytest.approx(0.02 * 111.32 * np.cos(np.radians(42.705)), rel=0.01)
    assert d.loc[1, "road_length_km_primary"] / total_l1 == pytest.approx(0.25, abs=0.005) and d.loc[2, "road_length_km_primary"] / total_l1 == pytest.approx(0.5, abs=0.005)
    assert d.loc[4, "road_length_km_tertiary"] == pytest.approx(0.006 * 110.57, rel=0.01) and d.loc[4, "road_length_km_primary"] == 0
    assert d.loc[5, "road_length_km_local_streets"] > 0 and d.loc[6, "road_length_km_secondary"] > 0 and d.loc[6, "road_length_km_other_unlisted"] > 0
    area = grid.set_index("cell_id")["area_km2"]
    assert d.loc[2, "road_density_km_per_km2_primary"] == pytest.approx(d.loc[2, "road_length_km_primary"] / area[2])
    assert d["road_length_km_total"].sum() == pytest.approx(rep["total_length_km_clipped"])
    assert rep["unlisted_fclass"] == ["ferry"] and rep["cells_with_roads"] == 6
    assert not any("traffic" in c for c in dens.columns) and "NOT traffic" in rep["definition"]


def test_road_with_missing_geometry_is_not_used_but_not_lost(grid):
    roads = _roads(grid)
    assert roads["geom_status"].tolist().count("null") == 1 and len(roads) == 6          # kept and flagged
    _, rep = gg.road_density(roads, grid)
    assert rep["segments_input"] == 6 and rep["segments_used"] == 5                        # only excluded from length sums
    assert rep["by_group"]["non_motorised_ways"]["features"] == 0


def test_every_osm_class_maps_to_one_group():
    flat = [f for fs in gg.ROAD_GROUPS.values() for f in fs]
    assert len(flat) == len(set(flat))
    assert gg.classify_road(pd.Series(["trunk_link", "residential", "footway", "ferry"])).tolist() == ["primary", "local_streets", "non_motorised_ways", "other_unlisted"]


def test_source_density_csv_is_compared_and_preserved(tmp_path, grid):
    dens, _ = gg.road_density(_roads(grid), grid)
    H.write_density_csv(tmp_path, dens)
    m, rep = gg.compare_source_density(dens, tmp_path / "roads_density_byclass_bishkek.csv")
    assert rep["src_csv_km_primary"]["corr"] == pytest.approx(1.0) and rep["src_csv_km_total"]["max_abs_diff_km"] == pytest.approx(0, abs=1e-9)
    assert "km of road per cell" in rep["interpretation"] or "kilometres of road per cell" in rep["interpretation"]
    assert {"src_csv_km_tertiary_broad", "src_csv_km_non_motorised_ways"} <= set(m.columns) and len(m) == 6
    pd.DataFrame({"Cell ID": [1]}).to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(gv.GisError, match="lacks columns"):
        gg.compare_source_density(dens, tmp_path / "bad.csv")


def test_cell_split_layers_are_checked_against_their_cell_ids(tmp_path, grid):
    H.write_roads(tmp_path, SPEC)
    master = gpd.read_file(tmp_path / "grids_bishkek_roads.shp").set_crs(4326)
    prim = gpd.read_file(tmp_path / "grids_bishkek_primary.shp").set_crs(4326)
    rep = gg.check_cell_split_layer(prim, master, grid, gg.ROAD_GROUPS["primary"], "primary")
    assert rep["segments"] == 3 and rep["segments_inside_their_Maille_cell"] == 3 and rep["osm_ids_equal_master_classes"] is True
    assert rep["length_attribute_over_utm_km_median_ratio"] < 0.05 and "degrees" in rep["length_attribute_unit_note"]
    wrong = prim.copy()
    wrong["Maille"] = 6
    assert gg.check_cell_split_layer(wrong, master, grid, gg.ROAD_GROUPS["primary"], "p")["segments_inside_their_Maille_cell"] < 3


# ------------------------------------------------------------------ context layers, admin, integration
def test_context_layers_counts_fractions_and_attribute_quality(tmp_path, grid):
    H.write_kmls(tmp_path, SPEC)
    ctx, reps = gg.read_context_layers(tmp_path)
    assert set(reps) == {"bte_boilers", "brick_kilns", "indus_area1", "indus_area2", "water_bodies", "dh_districts"}
    assert reps["bte_boilers"]["named_features"] == 2 and reps["brick_kilns"]["placeholder_names"] == 2
    assert reps["dh_districts"]["meaning_status"] == "unverified" and reps["indus_area1"]["meaning_status"] == "documented_in_record"
    assert ctx.crs.to_epsg() == 4326 and set(ctx["geom_status"]) == {"ok"} and "tessellate" not in ctx.columns
    cg, rep = gg.context_grid(ctx, grid)
    c = cg.set_index("cell_id")
    assert c["n_bte_boilers"].tolist() == [1, 1, 0, 0, 0, 0] and rep["kilns_counted"] == 1 and c.loc[6, "n_brick_kilns"] == 1     # kiln off the grid is not counted
    assert c.loc[4, "frac_industrial_areas"] == pytest.approx(0.5, abs=0.02)                                                       # two identical polygons: union, not 1.0
    assert c.loc[5, "frac_water_bodies"] == pytest.approx(1.0, abs=0.02) and c.loc[1, "frac_water_bodies"] == 0
    assert c.loc[[1, 2, 3], "frac_dh_districts_unverified"].round(2).tolist() == [1.0, 1.0, 1.0] and (c["frac_quarries"] == 0).all()


def test_context_feature_with_missing_geometry_is_kept_but_ignored(tmp_path, grid):
    H.write_kmls(tmp_path, SPEC)
    ctx, _ = gg.read_context_layers(tmp_path)
    extra = gpd.GeoDataFrame({"layer": ["bte_boilers"], "geom_status": ["null"], "layer_label": ["x"]}, geometry=[None], crs=4326)
    ctx2 = pd.concat([ctx, extra], ignore_index=True)
    assert (ctx2["geom_status"] == "null").sum() == 1 and len(ctx2) == len(ctx) + 1
    cg, rep = gg.context_grid(gpd.GeoDataFrame(ctx2, geometry="geometry", crs=4326), grid)
    assert rep["boilers_counted"] == 2                                              # null geometry cannot be counted, and nothing crashed


def test_admin_polygon_level_coverage_and_cell_fraction(tmp_path, grid):
    H.write_admin(tmp_path, SPEC)
    admin, rep = gg.read_admin(tmp_path / "bishkek_adm2.shp", grid)
    assert rep["crs"]["status"] == "declared_wgs84" and rep["distinct_codes"] == ["KGZ"] and "NOT Bishkek" in rep["level_note"]
    assert rep["share_of_grid_box_covered_pct"] == pytest.approx(100 * 0.025 / 0.03, abs=1.0)
    assert admin["admin_level_inferred"].iloc[0].startswith("country")
    k = gg.kgz_fraction(admin, grid).set_index("cell_id")["frac_in_kgz_polygons"]
    assert k[[1, 2, 4, 5]].round(2).tolist() == [1.0] * 4 and k[[3, 6]].round(2).tolist() == [0.5, 0.5]


def _pieces(tmp_path, grid, drop_pop_cell=None):
    H.write_population(tmp_path / "p.csv", SPEC, drop_cell=drop_pop_cell)
    H.write_camx(tmp_path / "c.csv", SPEC)
    H.write_emissions(tmp_path / "e", SPEC)
    H.write_kmls(tmp_path / "k", SPEC)
    H.write_admin(tmp_path / "a", SPEC)
    pop, _ = gg.parse_population(tmp_path / "p.csv", grid, SPEC)
    camx, _ = gg.parse_camx(tmp_path / "c.csv", grid, SPEC)
    area, _, _, tables = gg.read_emissions_area(tmp_path / "e", grid, SPEC)
    pts, _ = gg.read_emissions_points(tmp_path / "e", grid, tables, SPEC)
    dens, _ = gg.road_density(_roads(grid), grid)
    ctx, _ = gg.read_context_layers(tmp_path / "k")
    cg, _ = gg.context_grid(ctx, grid)
    admin, _ = gg.read_admin(tmp_path / "a" / "bishkek_adm2.shp", grid)
    return pop, camx, area, pts, dens, cg, gg.kgz_fraction(admin, grid)


def test_integration_one_row_per_cell_with_sources_kept_separate(tmp_path, grid):
    pop, camx, area, pts, dens, cg, kgz = _pieces(tmp_path, grid)
    g, rep = gg.integrate(grid, pop, dens, camx, area, pts, cg, kgz, 2019)
    assert rep["one_row_per_cell"] and len(g) == 6 and g["cell_id"].tolist() == [1, 2, 3, 4, 5, 6] and g.crs.to_epsg() == 4326
    assert g["pop_2019"].tolist() == [105, 205, 305, 405, 505, 605]                          # latest year only; the year is in the column name
    assert g["camx_pm25_annual_mean"].round(2).tolist() == [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
    assert g["emis_pm25_total_t_yr"].sum() == 58 and g["emis_point_sources_pm25_t_yr"].sum() == 40 and g["n_point_sources"].sum() == 3
    assert g["emis_pm25_total_t_yr"].sum() == area["pm25_total_t_yr"].sum()                  # point sources are NOT added on top
    assert g["emis_point_sources_pm25_t_yr"].le(g["emis_pm25_total_t_yr"]).all()
    assert {"emis_pm25_vehexh_t_yr", "road_density_km_per_km2_primary", "n_bte_boilers", "frac_in_kgz_polygons", "camx_pm25_jan"} <= set(g.columns)
    assert "emis_pm25_total_t_yr" in g.columns and g.columns.tolist().count("emis_pm25_total_t_yr") == 1
    assert rep["missing_values_per_column"] == {} and rep["population_year_used"] == 2019


def test_integration_reports_missing_values_instead_of_filling_them(tmp_path, grid):
    pop, camx, area, pts, dens, cg, kgz = _pieces(tmp_path, grid, drop_pop_cell=2)
    g, rep = gg.integrate(grid, pop, dens, camx, area, pts, cg, kgz, 2019)
    assert rep["missing_values_per_column"] == {"pop_2019": 1} and np.isnan(g.set_index("cell_id").loc[2, "pop_2019"])


def test_data_dictionary_documents_every_column_and_data_kind(tmp_path, grid):
    pop, camx, area, pts, dens, cg, kgz = _pieces(tmp_path, grid)
    g, _ = gg.integrate(grid, pop, dens, camx, area, pts, cg, kgz, 2019)
    d = {e["column"]: e for e in gr.data_dictionary(list(g.columns), 2019)}
    assert set(d) == set(g.columns) and all(e["definition"] != "(undocumented column)" for e in d.values())
    assert "MODELLED" in d["camx_pm25_jan"]["data_kind"] and "MODELLED" in d["emis_pm25_total_t_yr"]["data_kind"]
    assert "NOT traffic" in d["road_density_km_per_km2_primary"]["data_kind"] and d["pop_2019"]["vintage"] == "2019"
    assert "not independent" in d["camx_pm25_jan"]["note"] and "do not add" in d["emis_pm25_total_t_yr"]["note"].lower()
    assert "unverified" in d["frac_dh_districts_unverified"]["note"].lower()
