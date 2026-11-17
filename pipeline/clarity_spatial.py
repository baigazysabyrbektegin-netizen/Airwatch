"""Exploratory spatial summaries of the 2021 Clarity network (no predictions, no causal claims).

Method (also written into the report):
* For every hour with at least ``min_sensors`` readings, the city median of that hour is taken and each
  sensor's reading is expressed as ln(reading / city median). Averaged over a period this gives one
  relative level ``a_i`` per sensor (negative = cleaner than the city median).
* The empirical semivariance is gamma(h) = 0.5 * mean[(a_i - a_j)^2] over sensor pairs in a distance bin.
* An exponential model gamma(h) = nugget + psill * (1 - exp(-h / r)) is fitted to the pairs by least squares.
* A Mantel-type permutation test asks whether |a_i - a_j| increases with distance more than chance.
Assumptions and limits: one year; sensors are uncalibrated against each other; the sensors sit in the
built-up area only; 33 points give 528 non-independent pairs. Results describe THIS dataset and are not a
universal sensor radius.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.clarity_clean import haversine_km, spearman

PERIODS = {
    "winter_nov_dec": {11, 12},          # the only heating-season months in the file (no Jan/Feb data)
    "summer_jun_aug": {6, 7, 8},
    "all_mar_dec": set(range(3, 13)),
}
BINS_KM = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 6), (6, 9), (9, 25)]
MIN_SENSORS_FOR_SPATIAL = 5


def distance_matrix(lat, lon) -> np.ndarray:
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])


def convex_hull_area_km2(lat, lon) -> float:
    """Area of the convex hull in a local equirectangular projection (km^2)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if len(lat) < 3:
        return 0.0
    x = (lon - lon.mean()) * 111.32 * np.cos(np.radians(lat.mean()))
    y = (lat - lat.mean()) * 110.57
    pts = sorted(set(zip(x.round(9), y.round(9))))
    if len(pts) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    area = 0.0
    for i in range(len(hull)):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % len(hull)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def geometry_summary(inv_all: pd.DataFrame, inv_active: pd.DataFrame) -> dict:
    out: dict = {}
    for name, inv in (("all_listed", inv_all), ("with_data", inv_active)):
        v = inv[inv["flag_coordinates_valid"]]
        if len(v) < 2:
            out[name] = {"n": int(len(v))}
            continue
        D = distance_matrix(v["latitude"], v["longitude"])
        np.fill_diagonal(D, np.inf)
        nn = D.min(axis=1)
        pair = D[np.triu_indices(len(v), 1)]
        area = convex_hull_area_km2(v["latitude"], v["longitude"])
        out[name] = {
            "n": int(len(v)),
            "lat_range": [float(v["latitude"].min()), float(v["latitude"].max())],
            "lon_range": [float(v["longitude"].min()), float(v["longitude"].max())],
            "nearest_neighbour_km": {"min": float(nn.min()), "median": float(np.median(nn)), "mean": float(nn.mean()), "max": float(nn.max())},
            "pair_distance_km": {"p5": float(np.quantile(pair, .05)), "median": float(np.median(pair)), "p95": float(np.quantile(pair, .95)), "max": float(pair.max())},
            "convex_hull_area_km2": float(area),
            "devices_per_km2_in_hull": float(len(v) / area) if area > 0 else None,
            "quadrant_counts_workbook_rule": {k: int(n) for k, n in v["quadrant_rule"].value_counts().items()},
            "n_sharing_identical_coordinates": int(v["flag_shared_coordinates"].sum()),
        }
    return out


def device_levels(hourly: pd.DataFrame, months: set[int], min_sensors: int = 20, min_hours: int = 300) -> pd.DataFrame:
    """Mean ln(reading / city median) per device over a period, plus the number of hours used."""
    h = hourly[hourly["ts_start_local"].dt.month.isin(months) & (hourly["pm25_ugm3"] > 0)].copy()
    h["t"] = h["ts_start_utc"].astype("int64")
    cnt = h.groupby("t")["pm25_ugm3"].transform("size")
    h = h[cnt >= min_sensors]
    med = h.groupby("t")["pm25_ugm3"].transform("median")
    h["lr"] = np.log(h["pm25_ugm3"] / med)
    g = h.groupby("device_id").agg(a=("lr", "mean"), n_hours=("lr", "size"), mean_pm25=("pm25_ugm3", "mean"))
    return g[g["n_hours"] >= min_hours].reset_index()


def semivariogram(a: np.ndarray, D: np.ndarray, bins=BINS_KM) -> pd.DataFrame:
    iu = np.triu_indices(len(a), 1)
    d = D[iu]
    g = 0.5 * (a[iu[0]] - a[iu[1]]) ** 2
    rows = []
    for lo, hi in bins:
        m = (d >= lo) & (d < hi)
        if m.sum():
            rows.append({"bin_km": f"{lo}-{hi}", "n_pairs": int(m.sum()), "mean_distance_km": float(d[m].mean()), "semivariance": float(g[m].mean())})
    return pd.DataFrame(rows, columns=["bin_km", "n_pairs", "mean_distance_km", "semivariance"])


def fit_exponential(a: np.ndarray, D: np.ndarray) -> dict:
    """Least-squares exponential model on all pairs (grid search over the range parameter)."""
    iu = np.triu_indices(len(a), 1)
    d, g = D[iu], 0.5 * (a[iu[0]] - a[iu[1]]) ** 2
    best = None
    for r in np.arange(0.5, 30.01, 0.25):
        X = np.column_stack([np.ones_like(d), 1 - np.exp(-d / r)])
        coef, *_ = np.linalg.lstsq(X, g, rcond=None)
        coef = np.clip(coef, 0, None)
        sse = float(((X @ coef - g) ** 2).sum())
        if best is None or sse < best["sse"]:
            best = {"range_param_km": float(r), "nugget": float(coef[0]), "partial_sill": float(coef[1]), "sse": sse}
    best["practical_range_km_3r"] = 3 * best["range_param_km"]
    best["note"] = "Weakly identified with 33 sensors; treat as descriptive."
    return best


def distance_at_fraction_of_sill(sem: pd.DataFrame, sill: float, frac: float) -> float | None:
    """Linear interpolation (over bin mean distances) of where semivariance first reaches frac * sill."""
    target = frac * sill
    x, y = sem["mean_distance_km"].to_numpy(), sem["semivariance"].to_numpy()
    if len(x) == 0:
        return None
    if y[0] >= target:
        return float(x[0])
    for i in range(1, len(x)):
        if y[i] >= target > y[i - 1]:
            return float(x[i - 1] + (target - y[i - 1]) * (x[i] - x[i - 1]) / (y[i] - y[i - 1]))
    return None


def mantel(a: np.ndarray, D: np.ndarray, n_perm: int = 999, seed: int = 20210101) -> dict:
    """Pearson and Spearman correlation between pair distance and |difference|, with a permutation p-value."""
    iu = np.triu_indices(len(a), 1)
    diff = np.abs(a[:, None] - a[None, :])
    r_obs = float(np.corrcoef(D[iu], diff[iu])[0, 1])
    rho_obs = spearman(D[iu], diff[iu])
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(n_perm):
        p = rng.permutation(len(a))
        r = np.corrcoef(D[iu], diff[np.ix_(p, p)][iu])[0, 1]
        ge += r >= r_obs
    return {"pearson_r": r_obs, "spearman_rho": float(rho_obs), "n_perm": n_perm, "p_one_sided": float((ge + 1) / (n_perm + 1)), "seed": seed}


def analyse_period(levels: pd.DataFrame, inv: pd.DataFrame) -> dict:
    lv = levels.merge(inv[["device_id", "latitude", "longitude"]], on="device_id")
    if len(lv) < MIN_SENSORS_FOR_SPATIAL:
        return {"n_sensors": int(len(lv)), "insufficient_data": True,
                "note": f"fewer than {MIN_SENSORS_FOR_SPATIAL} sensors have enough hours in this period; no spatial statistics computed"}
    a = lv["a"].to_numpy()
    D = distance_matrix(lv["latitude"], lv["longitude"])
    sem = semivariogram(a, D)
    sill = float(np.var(a))
    plateau = sem[sem["mean_distance_km"] >= 9]
    return {
        "n_sensors": int(len(lv)), "n_pairs": int(len(a) * (len(a) - 1) / 2),
        "sd_log_ratio": float(np.std(a, ddof=1)), "implied_between_sensor_spread_pct": float(100 * (np.exp(np.std(a, ddof=1)) - 1)),
        "variance_log_ratio_sill_ref": sill,
        "plateau_semivariance_ge_9km": float(plateau["semivariance"].mean()) if len(plateau) else None,
        "mean_pm25_range": [float(lv["mean_pm25"].min()), float(lv["mean_pm25"].max())],
        "cv_of_device_mean_pm25": float(lv["mean_pm25"].std(ddof=1) / lv["mean_pm25"].mean()),
        "semivariogram": sem.to_dict("records"),
        "exponential_fit": fit_exponential(a, D),
        "distance_km_at_25pct_of_sill": distance_at_fraction_of_sill(sem, sill, 0.25),
        "distance_km_at_50pct_of_sill": distance_at_fraction_of_sill(sem, sill, 0.50),
        "distance_km_at_90pct_of_sill": distance_at_fraction_of_sill(sem, sill, 0.90),
        "mantel": mantel(a, D),
    }


def sd_ratio_bootstrap(winter: pd.DataFrame, summer: pd.DataFrame, n_boot: int = 1000, seed: int = 7) -> dict:
    """Bootstrap (resampling sensors) of SD(winter levels) / SD(summer levels), for sensors present in both."""
    m = winter.merge(summer, on="device_id", suffixes=("_w", "_s"))
    if len(m) < 5:
        return {"n": int(len(m))}
    rng = np.random.default_rng(seed)
    w, s = m["a_w"].to_numpy(), m["a_s"].to_numpy()
    est = np.std(w, ddof=1) / np.std(s, ddof=1)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(m), len(m))
        sw, ss = np.std(w[i], ddof=1), np.std(s[i], ddof=1)
        if ss > 0:
            boots.append(sw / ss)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    return {"n_sensors": int(len(m)), "estimate": float(est), "ci95": [float(lo), float(hi)], "n_boot": n_boot, "seed": seed}


def spatial_analysis(hourly: pd.DataFrame, inventory: pd.DataFrame) -> dict:
    active = inventory[inventory["status"] == "has_data_in_file"]
    res: dict = {"geometry": geometry_summary(inventory, active), "periods": {}}
    levels = {}
    for name, months in PERIODS.items():
        levels[name] = device_levels(hourly, months)
        res["periods"][name] = {"months": sorted(months), **analyse_period(levels[name], active)}
    res["winter_vs_summer_sd_ratio"] = sd_ratio_bootstrap(levels["winter_nov_dec"], levels["summer_jun_aug"])
    res["min_sensors_for_spatial"] = MIN_SENSORS_FOR_SPATIAL
    res["method"] = __doc__
    return res
