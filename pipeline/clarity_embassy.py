"""Exploratory comparison of Clarity sensors near the (approximate) Embassy location with the processed
Embassy series. Nothing here calibrates a sensor or settles the timestamp convention.

Alignment parameter ``k``: hours ADDED to the Clarity label instant before matching it to the Embassy
timestamp as given in its file. k = 0 pairs identical label instants; k = +1 pairs the Clarity label
instant + 1 h with the Embassy stamp. Neither is declared correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import manifest as mf
from pipeline.clarity_clean import haversine_km, spearman

# OpenStreetMap building point for the US Embassy (Nominatim lookup, ODbL). This is the embassy
# *building*, not a surveyed location of the air monitor, so it is APPROXIMATE.
EMBASSY_APPROX = {
    "latitude": 42.8272774,
    "longitude": 74.5824625,
    "source": "OpenStreetMap (Nominatim) building point for the US Embassy, 171 Chyngyz Aitmatov Ave; ODbL",
    "status": "APPROXIMATE - not a verified monitor location",
}
SHIFTS = (-2, -1, 0, 1, 2)
PERIODS = {"all_year": set(range(1, 13)), "winter_nov_dec": {11, 12}, "summer_jun_aug": {6, 7, 8}}


class EmbassyComparisonError(Exception):
    pass


def load_embassy_hourly(parquet_path: Path, processing_manifest_path: Path | None) -> pd.DataFrame:
    """Read the processed Embassy table (read-only), verifying it against its manifest when one is given."""
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise EmbassyComparisonError(f"Embassy hourly file not found: {parquet_path}")
    if processing_manifest_path is not None:
        m = mf.load_manifest(Path(processing_manifest_path))
        if m is None:
            raise EmbassyComparisonError(f"Embassy processing manifest not found: {processing_manifest_path}")
        rec = next((o for o in m["outputs"] if o["path"].endswith(parquet_path.name)), None)
        if rec is None:
            raise EmbassyComparisonError(f"{parquet_path.name} is not listed in {processing_manifest_path}")
        if mf.hash_file(parquet_path, ("sha256",))["sha256"] != rec["sha256"]:
            raise EmbassyComparisonError(f"{parquet_path} does not match its recorded checksum; not using it.")
    cols = ["ts_utc", "ts_local", "quality_flag", "pm25_model_ugm3", "flag_local_drop", "flag_local_spike"]
    return pd.read_parquet(parquet_path, columns=cols)


def nearest_devices(inventory: pd.DataFrame, radius_km: float = 2.0) -> pd.DataFrame:
    v = inventory[inventory["flag_coordinates_valid"]].copy()
    v["distance_to_embassy_approx_km"] = haversine_km(v["latitude"], v["longitude"], EMBASSY_APPROX["latitude"], EMBASSY_APPROX["longitude"])
    return v[v["distance_to_embassy_approx_km"] <= radius_km].sort_values("distance_to_embassy_approx_km")[
        ["device_id", "status", "latitude", "longitude", "distance_to_embassy_approx_km"]
    ].reset_index(drop=True)


def regression(x: np.ndarray, y: np.ndarray) -> dict:
    """y = Clarity, x = Embassy. OLS slope/intercept, Pearson r, mean bias and median ratio."""
    n = len(x)
    if n < 10 or np.std(x) == 0 or np.std(y) == 0:
        return {"n": int(n)}
    r = float(np.corrcoef(x, y)[0, 1])
    slope, intercept = np.polyfit(x, y, 1)
    return {
        "n": int(n), "pearson_r": r, "r_squared": r * r, "slope": float(slope), "intercept": float(intercept),
        "spearman_rho": spearman(x, y), "mean_clarity": float(y.mean()), "mean_embassy": float(x.mean()),
        "mean_difference_clarity_minus_embassy": float((y - x).mean()), "median_ratio_clarity_to_embassy": float(np.median(y[x > 0] / x[x > 0])) if (x > 0).any() else None,
    }


def paired(clarity_dev: pd.DataFrame, embassy: pd.DataFrame, k: int, months: set[int],
           exclude_flagged: bool = False) -> pd.DataFrame:
    e = embassy[(embassy["quality_flag"] == "VALID") & embassy["pm25_model_ugm3"].notna()].copy()
    if exclude_flagged:
        e = e[~(e["flag_local_drop"] | e["flag_local_spike"])]
    c = clarity_dev.copy()
    if exclude_flagged:
        c = c[~(c["flag_high_value"] | c["flag_neighbor_outlier"])]
    c = c.assign(key=(c["ts_label_utc"] + pd.Timedelta(hours=k)).astype("int64"))[["key", "pm25_ugm3"]]
    e = e.assign(key=e["ts_utc"].astype("int64"), month=e["ts_local"].dt.month, date=e["ts_local"].dt.date)
    j = e.merge(c, on="key")
    return j[j["month"].isin(months)][["key", "date", "pm25_model_ugm3", "pm25_ugm3"]].rename(
        columns={"pm25_model_ugm3": "embassy", "pm25_ugm3": "clarity"})


def block_bootstrap_r(j: pd.DataFrame, n_boot: int = 300, seed: int = 11) -> list[float] | None:
    """95 % interval for r, resampling whole calendar days (keeps within-day autocorrelation)."""
    if len(j) < 50:
        return None
    days = {d: g[["embassy", "clarity"]].to_numpy() for d, g in j.groupby("date")}
    keys = list(days)
    rng = np.random.default_rng(seed)
    rs = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        arr = np.concatenate([days[keys[i]] for i in pick])
        if arr[:, 0].std() > 0 and arr[:, 1].std() > 0:
            rs.append(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1])
    return [float(np.quantile(rs, .025)), float(np.quantile(rs, .975))] if rs else None


def compare(hourly: pd.DataFrame, inventory: pd.DataFrame, embassy: pd.DataFrame, radius_km: float = 2.0) -> dict:
    near = nearest_devices(inventory, radius_km)
    result: dict = {"embassy_location": EMBASSY_APPROX, "nearby_devices": near.to_dict("records"), "comparisons": []}
    for dev in near.loc[near["status"] == "has_data_in_file", "device_id"]:
        cd = hourly[hourly["device_id"] == dev]
        dist = float(near.loc[near["device_id"] == dev, "distance_to_embassy_approx_km"].iloc[0])
        for pname, months in PERIODS.items():
            for k in SHIFTS:
                j = paired(cd, embassy, k, months)
                stats = regression(j["embassy"].to_numpy(), j["clarity"].to_numpy()) if len(j) else {"n": 0}
                if k in (0, 1) and stats.get("n", 0) >= 50 and pname == "all_year":
                    stats["pearson_r_ci95_day_block_bootstrap"] = block_bootstrap_r(j)
                jx = paired(cd, embassy, k, months, exclude_flagged=True)
                sens = regression(jx["embassy"].to_numpy(), jx["clarity"].to_numpy()) if len(jx) else {"n": 0}
                result["comparisons"].append({"device_id": dev, "distance_km": dist, "period": pname, "shift_k_hours": k,
                                              "all_valid": stats, "excluding_flagged_values": {x: sens.get(x) for x in ("n", "pearson_r", "slope")}})
    return result


def diurnal_evidence(hourly: pd.DataFrame, embassy: pd.DataFrame, months=(11, 12)) -> dict:
    """Which hour of the day is the network's evening peak? Used only to judge whether labels look like local time."""
    h = hourly[hourly["ts_start_local"].dt.month.isin(months)]
    cl = h.groupby("hour_label")["pm25_ugm3"].mean()
    e = embassy[(embassy["quality_flag"] == "VALID") & embassy["ts_local"].dt.month.isin(months) & (embassy["ts_local"].dt.year == 2021)]
    em = e.groupby(e["ts_local"].dt.hour)["pm25_model_ugm3"].mean()
    if cl.empty or em.empty:
        return None
    peak_c = int(cl.idxmax())
    return {
        "months": list(months),
        "clarity_network_mean_by_hour_label": {int(k): round(float(v), 1) for k, v in cl.items()},
        "embassy_mean_by_stamp_hour_2021": {int(k): round(float(v), 1) for k, v in em.items()},
        "clarity_peak_hour_label": peak_c,
        "embassy_peak_stamp_hour": int(em.idxmax()),
        "local_hour_if_clarity_labels_were_utc": int((peak_c + 6) % 24),
        "reading": ("The Clarity evening peak sits near the Embassy's evening peak when both are read as local clock hours; "
                    "if the Clarity labels were UTC the peak would fall in the early morning in Bishkek. This favours local time "
                    "but does not prove it, and says nothing about hour start versus end."),
    }


def dump_json(obj, path: Path) -> None:
    mf.write_manifest_atomic(path, json.loads(json.dumps(obj, default=str)))
