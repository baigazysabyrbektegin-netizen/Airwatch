"""Synthetic processed data for the API tests. Mock values only; they never touch the real datasets."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from app.api.deps import get_store
from app.main import create_app
from app.services.data_store import DataStore

TZ = "Asia/Bishkek"


def _w(df, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def build_clarity(root: Path):
    inv = pd.DataFrame({
        "device_id": ["C1", "C2", "C3", "C4"], "latitude": [42.86, 42.90, 42.80, 42.90], "longitude": [74.55, 74.60, 74.50, 74.60],
        "flag_coordinates_valid": True, "flag_shared_coordinates": [False, True, False, True], "shared_coordinate_group": [np.nan, 1.0, np.nan, 1.0],
        "status": ["has_data_in_file", "has_data_in_file", "no_data_in_file", "no_data_in_file"], "n_valid_readings": [48, 24, 0, 0]})
    p = root / "processed/clarity"
    p.mkdir(parents=True, exist_ok=True)
    inv.to_csv(p / "clarity_device_inventory.csv", index=False)

    def hours(dev, start, n, base, high_at=None):
        t = pd.date_range(start, periods=n, freq="h", tz=TZ)
        pm = base + np.arange(n, dtype=float)
        if high_at is not None:
            pm[high_at] = 600.0
        return pd.DataFrame({"device_id": dev, "ts_start_local": t, "ts_start_utc": t.tz_convert("UTC"), "ts_label_utc": t.tz_convert("UTC") + pd.Timedelta(hours=1),
                             "hour_label": (t.hour + 1).astype("int64"), "pm25_ugm3": pm, "flag_high_value": pm > 500, "flag_neighbor_outlier": pm > 500,
                             "neighbor_median_ugm3": 12.0, "neighbor_ratio": pm / 12.0, "n_neighbors": 5, "source_excel_row": np.arange(n) + 2, "processing_version": "test"})

    h = pd.concat([hours("C1", "2021-03-01 00:00", 48, 10.0, high_at=5), hours("C2", "2021-03-02 00:00", 24, 20.0)], ignore_index=True)
    _w(h, p / "clarity_pm25_hourly.parquet")
    daily = pd.DataFrame({
        "device_id": ["C1", "C1", "C1"], "date_local": pd.to_datetime(["2021-03-01", "2021-03-02", "2021-03-03"]), "n_valid_hours": [24, 24, 10],
        "meets_min_hours": [True, True, False], "pm25_daily_mean_ugm3": [21.0, 35.0, np.nan], "pm25_mean_all_valid": [21.0, 35.0, 15.0], "pm25_min": [10.0, 34.0, 12.0],
        "pm25_max": [600.0, 57.0, 20.0], "n_high_value_hours": [1, 0, 0], "n_neighbor_outlier_hours": [1, 0, 0], "min_valid_hours_required": 18})
    _w(daily, p / "clarity_daily.parquet")
    monthly = pd.DataFrame({"device_id": ["C1", "C1"], "year_month": ["2021-03", "2021-04"], "n_valid_hours": [48, 5], "n_valid_days": [2, 0], "eligible_days": [2, 30],
                            "valid_day_fraction": [1.0, 0.0], "meets_threshold": [True, False], "pm25_monthly_mean_ugm3": [28.0, np.nan], "min_day_fraction_required": 0.75})
    _w(monthly, p / "clarity_monthly.parquet")


def build_embassy(root: Path):
    t = pd.date_range("2023-03-31 00:00", periods=72, freq="h", tz=TZ)
    n = len(t)
    raw = 20.0 + np.arange(n, dtype=float)
    q = np.array(["VALID"] * n, dtype=object)
    present = np.ones(n, dtype=bool)
    model = raw.copy()
    raw[2], model[2] = -2.0, 0.0                     # negative noise: raw kept, model clipped
    raw[3], model[3] = 600.0, 600.0                  # high value
    q[4], model[4] = "INVALID", np.nan               # provider-invalid row
    present[5], q[5], raw[5], model[5] = False, "NO_ROW", np.nan, np.nan   # gap hour
    q[6], raw[6], model[6] = "DUP_CONFLICT", np.nan, np.nan
    e = pd.DataFrame({"ts_local": t, "ts_utc": t.tz_convert("UTC"), "ts_original": t.strftime("%Y-%m-%d %I:%M %p"), "row_present": present, "n_source_rows": present.astype(int),
                      "quality_flag": q, "qc_name_original": np.where(q == "INVALID", "Invalid", "Valid"), "pm25_raw_ugm3": raw, "pm25_model_ugm3": model,
                      "pm25_feature_interp_ugm3": model, "is_imputed": False, "flag_neg_noise": raw < 0, "flag_high_value": raw >= 500, "flag_local_drop": False,
                      "flag_local_spike": [False, False, False, True] + [False] * (n - 4), "flag_duplicate_ts": q == "DUP_CONFLICT", "flag_duplicate_conflict": q == "DUP_CONFLICT",
                      "source_file": "f.csv", "source_line": "1", "processing_version": "test"})
    _w(e, root / "processed/embassy/embassy_pm25_hourly.parquet")


WEATHER_START_UTC = "2021-02-27 00:00"     # hour index i == i hours after this UTC instant
WEATHER_HOURS = 24 * 770                    # to 2023-04-07


def build_weather(root: Path):
    """Synthetic ERA5-like hourly table. temperature_2m == the hour index, so tests can verify exactly which hour was paired."""
    idx = np.arange(WEATHER_HOURS)
    t = pd.date_range(WEATHER_START_UTC, periods=WEATHER_HOURS, freq="h", tz="UTC")
    d = pd.DataFrame({"ts_utc": t, "ts_local": t.tz_convert(TZ), "temperature_2m": idx.astype(float), "relative_humidity_2m": 40.0 + (idx % 24),
                      "wind_speed_10m": 1.0 + (idx % 5), "wind_direction_10m": np.where(idx % 2 == 0, 350.0, 10.0), "surface_pressure": 935.0,
                      "precipitation": np.where(idx % 24 == 0, 1.5, 0.0), "boundary_layer_height": 300.0})
    # a day (local 2021-03-05) with only 12 valid temperature hours -> its daily value must be null, never a partial mean
    day = (d["ts_local"] >= pd.Timestamp("2021-03-05", tz=TZ)) & (d["ts_local"] < pd.Timestamp("2021-03-06", tz=TZ))
    d.loc[d.index[day][12:], "temperature_2m"] = np.nan
    # boundary layer height missing for a whole month, as in the real ERA5 response for 2024
    d.loc[(d["ts_local"] >= pd.Timestamp("2021-05-01", tz=TZ)) & (d["ts_local"] < pd.Timestamp("2021-06-01", tz=TZ)), "boundary_layer_height"] = np.nan
    _w(d, root / "processed/weather/era5_hourly.parquet")


def build_gis(root: Path):
    p = root / "processed/gis"
    p.mkdir(parents=True, exist_ok=True)
    cells = []
    for iy in (1, 2):
        for ix in (1, 2, 3):
            cid = (iy - 1) * 3 + ix
            x0, y0 = 74.35 + 0.01 * (ix - 1), 42.70 + 0.01 * (iy - 1)
            row = {"cell_id": cid, "ix": ix, "iy": iy, "lon_c": x0 + 0.005, "lat_c": y0 + 0.005, "area_km2": 0.9, "pop_2019": cid * 1000.0 if cid != 4 else np.nan,
                   "road_density_km_per_km2_total": cid * 1.5, "emis_pm25_total_t_yr": cid * 2.0, "emis_pm25_total_t_per_km2_yr": cid * 2.0 / 0.9,
                   "n_point_sources": cid % 2, "frac_in_kgz_polygons": 1.0, "camx_pm25_annual_mean": 10.0 + cid, "geometry": box(x0, y0, x0 + 0.01, y0 + 0.01)}
            for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                row[f"camx_pm25_{m}"] = float(cid * 10 + i)
            cells.append(row)
    gpd.GeoDataFrame(cells, geometry="geometry", crs=4326).to_parquet(p / "airwatch_spatial_grid.parquet", index=False)
    roads = gpd.GeoDataFrame({
        "osm_id": ["1", "2", "3", "4", "5"], "fclass": ["primary", "secondary", "tertiary", "residential", "footway"],
        "road_group": ["primary", "secondary", "tertiary", "local_streets", "non_motorised_ways"], "name": "????", "oneway": "B", "bridge": "F", "tunnel": "F",
        "geometry": [LineString([(74.35 + 0.002 * i, 42.70), (74.36 + 0.002 * i, 42.71)]) for i in range(5)]}, crs=4326)
    roads.to_parquet(p / "roads.parquet", index=False)
    ctx = gpd.GeoDataFrame({"layer": ["bte_boilers", "bte_boilers", "brick_kilns", "dh_districts"], "layer_label": ["Boilers", "Boilers", "Kilns", "District heating?"],
                            "layer_meaning_status": ["inferred_from_file_name", "inferred_from_file_name", "inferred_from_file_name", "unverified"],
                            "feature_name": ["Boiler A", "Boiler B", None, None], "name_is_placeholder": [False, False, True, True], "geom_status": "ok",
                            "geom_invalid_reason": "", "source_file": "x.kml", "feature_index": [1, 2, 1, 1],
                            "geometry": [Point(74.36, 42.8), Point(74.37, 42.81), Point(74.4, 42.9), box(74.35, 42.7, 74.38, 42.72)]}, crs=4326)
    ctx.to_file(p / "context_layers.geojson", driver="GeoJSON")
    adm = gpd.GeoDataFrame({"admin_code": ["KGZ"], "admin_name": ["Kyrgyzstan"], "admin_level_inferred": ["country (adm0)"], "geom_status": "ok", "feature_index": [1], "source_file": "a.shp",
                            "geometry": [box(74.35, 42.7, 74.38, 42.72)]}, crs=4326)
    adm.to_file(p / "administrative_boundaries.geojson", driver="GeoJSON")


def build_manifests(root: Path):
    m = root / "manifest"
    m.mkdir(parents=True, exist_ok=True)
    (m / "zenodo_12720883.json").write_text(json.dumps({"record": {"record_id": 12720883, "doi": "10.5281/zenodo.12720883", "title": "Test record", "publication_date": "2024-07-11",
                                                                    "licence": {"id": "cc-by-4.0"}, "creators": [{"name": "Doe, J"}], "record_url": "https://zenodo.org/records/12720883"}}))
    (m / "embassy_processing.json").write_text(json.dumps({"processing": {"version": "embassy-test"}, "source_archive": {"provenance_review_required": True},
                                                            "dataset": {"first_timestamp_local": "2019-02-06 06:00:00+06:00", "last_timestamp_local": "2024-06-30 23:00:00+06:00", "quality_flag_counts_hourly": {"VALID": 3}},
                                                            "known_limitations": ["Embassy limitation one", "QC Valid is not homogeneous over time"]}))
    (m / "clarity_processing.json").write_text(json.dumps({"processing": {"version": "clarity-test"}, "licence": {"provenance_review_required": True, "attribution": "Guttikunda (2024)"},
                                                            "dataset_summary": {"first_interval_start_local": "2021-02-28 01:00:00+06:00", "last_interval_start_local": "2021-12-31 23:00:00+06:00",
                                                                                "devices_with_data": 33, "devices_listed": 56},
                                                            "timestamp_assumptions": {"hour_convention": "hour_ending", "status": "ASSUMPTION - undocumented"},
                                                            "known_limitations": ["Clarity limitation one"]}))
    (m / "weather_processing.json").write_text(json.dumps({
        "source": {"provider": "Open-Meteo", "model": "era5", "spatial_resolution": "about 0.25 degrees (~25 km)", "dataset_version": "not reported by the API"},
        "retrieval": {"retrieved_at_utc": "2026-11-11T05:46:34+00:00", "response_sha256": "abc"},
        "grid": {"requested_latitude": 42.917, "requested_longitude": 74.51, "returned_latitude": 43.0, "returned_longitude": 74.5, "elevation_m": 702.0},
        "variables": {"temperature_2m": {"unit": "°C"}, "wind_speed_10m": {"unit": "m/s"}, "precipitation": {"unit": "mm"}},
        "validation": {"first_utc": "2021-02-27T00:00:00+00:00", "last_utc": "2023-04-07T23:00:00+00:00"},
        "licence": {"attribution": {"open_meteo": "Weather data by Open-Meteo.com, CC BY 4.0.", "copernicus": "Contains Copernicus Climate Change Service information."}},
        "known_limitations": ["Weather limitation one"]}))
    (m / "gis_processing.json").write_text(json.dumps({"processing": {"version": "gis-test"}, "output_crs": "EPSG:4326", "data_kinds": {"camx": "MODELLED", "population": "gridded estimate", "roads": "static geographic",
                                                                                                "emissions": "MODELLED inventory", "context_layers": "static geographic (mapped)"},
                                                        "data_vintage": {"camx": "year not stated", "emissions": "2018", "population": "2000-2019", "roads": "not stated", "context_layers": "not stated"},
                                                        "spatial_resolution": {"grid_cells": "3 x 2", "cell_size_deg": 0.01}, "known_limitations": ["GIS limitation one"],
                                                        "licence": {"notes": ["OSM ODbL note"]},
                                                        "grid_columns": [{"column": "pop_2019", "definition": "population 2019", "unit": "persons/cell", "data_kind": "gridded population ESTIMATE", "source": "s", "vintage": "2019"},
                                                                         {"column": "emis_pm25_total_t_yr", "definition": "emissions", "unit": "t/cell/yr", "data_kind": "MODELLED inventory", "source": "s", "vintage": "2018"}]}))


@pytest.fixture
def data_dir(tmp_path) -> Path:
    root = tmp_path / "data"
    build_clarity(root)
    build_embassy(root)
    build_gis(root)
    build_weather(root)
    build_manifests(root)
    (root / "raw").mkdir()
    (root / "raw" / "poison.csv").write_text("must never be read by the API")
    return root


@pytest.fixture
def store(data_dir) -> DataStore:
    return DataStore(data_dir)


@pytest.fixture
def client(store):
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path):
    """An API whose data directory has no files at all."""
    app = create_app()
    s = DataStore(tmp_path / "nothing")
    app.dependency_overrides[get_store] = lambda: s
    return TestClient(app)
