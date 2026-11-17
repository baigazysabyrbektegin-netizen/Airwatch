"""Monitoring stations built from the processed Clarity inventory/hourly table and the Embassy hourly table.

Nothing is invented: coordinates, periods and counts come from the processed files. The Embassy position is the
approximate OpenStreetMap building point recorded in the Stage 3 comparison, never a verified monitor coordinate.
"""

from __future__ import annotations

import pandas as pd

from app.services.data_store import LOCAL_TZ, DataStore
from app.services.errors import NotFound

EMBASSY_ID = "us-embassy-bishkek"
EMBASSY_LAT, EMBASSY_LON = 42.8272774, 74.5824625

CLARITY_TIMESTAMP_NOTE = ("Clarity timestamps are interval starts under an ASSUMED hour-ending convention in the Asia/Bishkek time zone. "
                          "The source documents neither the convention nor the time zone.")
EMBASSY_TIMESTAMP_NOTE = ("Embassy timestamps are the 'Date (LT)' values as given by the source in Asia/Bishkek local time; "
                          "whether they mark the start or the end of the hour is not documented.")
ALIGNMENT_NOTE = ("An apparent 1-hour offset between the Clarity and Embassy time labels is UNRESOLVED. This API never merges or aligns "
                  "series from different stations; each response contains one station on its own timestamps.")
REGIME_START, REGIME_END = pd.Timestamp("2023-04-01", tz=LOCAL_TZ), pd.Timestamp("2024-06-30 23:59:59", tz=LOCAL_TZ)
REGIME_NOTE = ("Embassy readings from about April 2023 to June 2024 show a potential measurement-regime issue (many near-zero 'valid' values, "
               "alternating with ~150 µg/m³ in some hours). They are flagged where detectable, not removed. Cause unverified.")


def iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(LOCAL_TZ)
    return t.tz_convert(LOCAL_TZ).isoformat()


def _clarity_stations(store: DataStore) -> list[dict]:
    inv = store.clarity_inventory()
    hourly = store.clarity_hourly()
    t0, t1 = hourly["ts_start_utc"].min(), hourly["ts_start_utc"].max()
    expected = int((t1 - t0) / pd.Timedelta(hours=1)) + 1
    agg = hourly.groupby("device_id").agg(n=("pm25_ugm3", "size"), first=("ts_start_local", "min"), last=("ts_start_local", "max"),
                                          n_high=("flag_high_value", "sum"), n_out=("flag_neighbor_outlier", "sum"), mean=("pm25_ugm3", "mean"))
    out = []
    shared = inv[inv["flag_shared_coordinates"].astype(bool)].groupby("shared_coordinate_group")["device_id"].apply(list).to_dict() if "shared_coordinate_group" in inv else {}
    for r in inv.itertuples(index=False):
        has = r.status == "has_data_in_file" and r.device_id in agg.index
        valid_coords = bool(r.flag_coordinates_valid)
        others = []
        if bool(r.flag_shared_coordinates) and not pd.isna(r.shared_coordinate_group):
            others = [d for d in shared.get(r.shared_coordinate_group, []) if d != r.device_id]
        notes = []
        if not has:
            notes.append("Listed with coordinates in the 2021 workbook but has no readings in the file (status no_data_in_file); not an active sensor.")
        if others:
            notes.append(f"Shares exactly the same coordinates as {len(others)} other listed device(s); the cause is unknown (possible placeholder position).")
        s = {
            "id": r.device_id, "name": r.device_id, "network": "clarity", "operator_reported": "Kyrgyz Hydrometeorological Agency (as reported in the source paper)",
            "sensor_type": "Clarity low-cost PM2.5 sensor", "grade": "low-cost", "data_kind": "measured (low-cost sensor)",
            "latitude": float(r.latitude) if valid_coords else None, "longitude": float(r.longitude) if valid_coords else None,
            "location_status": "listed in the Clarity 2021 workbook (original coordinates)" if valid_coords else "no valid coordinates in the source",
            "location_note": None, "data_available": bool(has), "status": r.status, "vintage": "2021-02-28 to 2021-12-31 (historical)",
            "data_period": None, "n_valid_readings": 0, "coverage_pct": None, "quality": None, "shares_coordinates_with": others, "notes": notes,
            "timestamp_convention": CLARITY_TIMESTAMP_NOTE,
        }
        if has:
            a = agg.loc[r.device_id]
            s["data_period"] = {"start": iso(a["first"]), "end": iso(a["last"])}
            s["n_valid_readings"] = int(a["n"])
            s["coverage_pct"] = round(100 * float(a["n"]) / expected, 1)
            s["quality"] = {"valid_readings": int(a["n"]), "coverage_pct_of_network_window": round(100 * float(a["n"]) / expected, 1),
                            "high_value_readings_over_500": int(a["n_high"]), "neighbor_outlier_candidates": int(a["n_out"]), "mean_pm25_ugm3": round(float(a["mean"]), 1),
                            "calibration": "The source paper reports periodic calibration; method and per-device records were not found. NOT independently verified."}
        out.append(s)
    return out


def _embassy_station(store: DataStore) -> dict:
    e = store.embassy_hourly()
    valid = e[e["quality_flag"] == "VALID"]
    present = e[e["row_present"]]
    n_present_span = len(e)
    s = {
        "id": EMBASSY_ID, "name": "US Embassy Bishkek monitor", "network": "us-embassy", "operator_reported": "US Embassy Bishkek",
        "sensor_type": "Embassy PM2.5 monitor (described as reference-grade in the source paper)", "grade": "reference (per source paper)",
        "data_kind": "measured (reference-grade monitor, per source paper)", "latitude": EMBASSY_LAT, "longitude": EMBASSY_LON,
        "location_status": "APPROXIMATE", "location_note": "OpenStreetMap building point for the US Embassy; NOT a verified monitor coordinate.",
        "data_available": True, "status": "has_data", "vintage": "2019-02-06 to 2024-06-30 (historical)",
        "data_period": {"start": iso(present["ts_local"].min()), "end": iso(present["ts_local"].max())}, "n_valid_readings": int(len(valid)),
        "coverage_pct": round(100 * len(valid) / n_present_span, 1), "shares_coordinates_with": [],
        "quality": {"valid_hours": int(len(valid)), "coverage_pct_of_timeline": round(100 * len(valid) / n_present_span, 1),
                    "negative_noise_hours": int(valid["flag_neg_noise"].sum()), "high_value_hours_over_500": int(valid["flag_high_value"].sum()),
                    "local_drop_hours": int(e["flag_local_drop"].sum()), "local_spike_hours": int(e["flag_local_spike"].sum()),
                    "duplicate_conflict_hours": int((e["quality_flag"] == "DUP_CONFLICT").sum()), "hours_without_source_row": int((~e["row_present"]).sum()),
                    "potential_regime_issue": REGIME_NOTE},
        "notes": [REGIME_NOTE], "timestamp_convention": EMBASSY_TIMESTAMP_NOTE,
    }
    return s


def all_stations(store: DataStore) -> list[dict]:
    def build():
        return [_embassy_station(store)] + _clarity_stations(store)

    return store.cached("stations", build)


def list_stations(store: DataStore, network: str | None = None, include_no_data: bool = False) -> list[dict]:
    st = all_stations(store)
    if network:
        st = [s for s in st if s["network"] == network]
    if not include_no_data:
        st = [s for s in st if s["data_available"]]
    return st


def get_station(store: DataStore, station_id: str) -> dict:
    for s in all_stations(store):
        if s["id"].lower() == station_id.lower():
            return s
    raise NotFound(f"Unknown station {station_id!r}.", [s["id"] for s in all_stations(store) if s["data_available"]][:60])
