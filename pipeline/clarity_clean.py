"""Cleaning, timestamp normalisation, device inventory, coverage and aggregates for the Clarity 2021 data.

Principles
* Original cell text is kept in ``src_*`` columns; nothing is overwritten or filled.
* Missing PM2.5 stays NaN (never 0). Readings are not calibrated, corrected or smoothed.
* The hour convention and time zone are NOT documented by the source, so they are configuration
  (``ClarityConfig``), with a provisional default that is recorded everywhere it is used.
* High or odd readings are flagged, never deleted. Records that cannot be timestamped stay in the audit data.
* No coordinates are invented; locations come only from the workbook's ``locations`` sheet.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

PROCESSING_VERSION = "clarity-clean-1.0.0"
LOCAL_TZ = "Asia/Bishkek"
HOUR_CONVENTIONS = ("hour_ending", "hour_start")
SOURCE_TIMEZONES = ("Asia/Bishkek", "UTC")
_INT = re.compile(r"^\s*[+-]?\d+(\.0+)?\s*$")

SRC_COLS = {
    "Device ID": "src_device_id", "PM25": "src_pm25", "year": "src_year", "month": "src_month",
    "day": "src_day", "hour": "src_hour", "Quadrant": "src_quadrant",
}


class ClarityCleanError(Exception):
    pass


@dataclass(frozen=True)
class ClarityConfig:
    """Everything that is an assumption or a tunable threshold lives here and is written to the manifest."""

    # --- timestamp interpretation (NOT documented by the source; provisional defaults)
    hour_convention: str = "hour_ending"      # label h (1-24) = interval ending at h:00; 24 -> 00:00 next day
    source_timezone: str = "Asia/Bishkek"    # time zone of the year/month/day/hour fields
    extra_shift_hours: int = 0               # sensitivity knob; 0 = never shift readings
    hour_label_min: int = 1
    hour_label_max: int = 24
    # --- quality thresholds
    high_value_ugm3: float = 500.0
    neighbor_ratio: float = 5.0              # reading > ratio * median of the other sensors, same hour
    neighbor_min_others: int = 5             # ...only judged when at least this many other sensors report
    daily_min_valid_hours: int = 18
    monthly_min_day_fraction: float = 0.75
    low_coverage_pct: float = 85.0
    # --- drift candidate rule (exploratory)
    drift_min_months: int = 6
    drift_min_abs_rho: float = 0.8
    drift_min_range_ratio: float = 1.5
    # --- workbook's own quadrant rule (documented in the 'locations' sheet formulas)
    quadrant_lat: float = 42.87
    quadrant_lon: float = 74.6
    # --- plausibility window for flagging coordinates (Bishkek airshed used by the Zenodo grid)
    domain_lat: tuple = (42.70, 43.10)
    domain_lon: tuple = (74.35, 74.85)

    def __post_init__(self):
        if self.hour_convention not in HOUR_CONVENTIONS:
            raise ClarityCleanError(f"hour_convention must be one of {HOUR_CONVENTIONS}")
        if self.source_timezone not in SOURCE_TIMEZONES:
            raise ClarityCleanError(f"source_timezone must be one of {SOURCE_TIMEZONES}")
        if not isinstance(self.extra_shift_hours, int):
            raise ClarityCleanError("extra_shift_hours must be an integer")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["domain_lat"], d["domain_lon"] = list(self.domain_lat), list(self.domain_lon)
        return d

    def timestamp_assumptions(self) -> dict:
        return {
            "hour_convention": self.hour_convention,
            "source_timezone": self.source_timezone,
            "extra_shift_hours": self.extra_shift_hours,
            "hour_label_range": [self.hour_label_min, self.hour_label_max],
            "status": "ASSUMPTION - the source does not document the convention or the time zone",
            "mapping": (
                "label instant = local midnight of (year,month,day) + hour hours (hour 24 -> 00:00 next day); "
                + ("interval start = label instant - 1 h (hour-ending)" if self.hour_convention == "hour_ending"
                   else "interval start = label instant (hour label read as the hour's start)")
                + f"; then + {self.extra_shift_hours} h extra shift (0 = none)"
            ),
        }


# ----------------------------------------------------------------------------- locations / inventory
def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def _to_float(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def quadrant_from_coords(lat: float, lon: float, cfg: ClarityConfig) -> str | None:
    """The workbook's own rule: Q1 = N&E, Q2 = S&E, Q3 = S&W, Q4 = N&W of (quadrant_lat, quadrant_lon)."""
    if lat is None or lon is None or (isinstance(lat, float) and math.isnan(lat)) or (isinstance(lon, float) and math.isnan(lon)):
        return None
    if lat == cfg.quadrant_lat or lon == cfg.quadrant_lon:
        return None  # exactly on a boundary: the workbook formula would give no quadrant
    n, e = lat > cfg.quadrant_lat, lon > cfg.quadrant_lon
    return "Q1" if n and e else "Q2" if (not n) and e else "Q3" if (not n) and (not e) else "Q4"


def build_inventory(locations: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """One row per row of the workbook's location list, original text kept, nothing invented."""
    loc = locations[locations["excel_row"] > 1].copy()
    loc = loc[loc["B"].notna()]
    inv = pd.DataFrame(
        {
            "device_id": loc["B"].str.strip().to_numpy(),
            "inventory_index_src": loc["A"].to_numpy(),
            "latitude_src": loc["C"].to_numpy(),
            "longitude_src": loc["D"].to_numpy(),
            "locations_excel_row": loc["excel_row"].to_numpy(),
        }
    )
    inv["latitude"] = _to_float(inv["latitude_src"])
    inv["longitude"] = _to_float(inv["longitude_src"])
    inv["flag_coordinates_valid"] = inv["latitude"].between(-90, 90) & inv["longitude"].between(-180, 180)
    inv["flag_in_study_domain"] = inv["latitude"].between(*cfg.domain_lat) & inv["longitude"].between(*cfg.domain_lon)
    inv["quadrant_rule"] = [quadrant_from_coords(a, b, cfg) if v else None
                            for a, b, v in zip(inv["latitude"], inv["longitude"], inv["flag_coordinates_valid"])]
    # the workbook's own lookup table (columns M:P) only covers devices that have readings
    lookup: dict[str, str] = {}
    if "M" in locations.columns and "P" in locations.columns:
        lk = locations[(locations["excel_row"] > 1) & locations["M"].notna() & locations["P"].notna()]
        lookup = dict(zip(lk["M"].str.strip(), lk["P"]))
    inv["quadrant_workbook_lookup"] = inv["device_id"].map(lookup)
    inv["quadrant_consistent"] = np.where(inv["quadrant_workbook_lookup"].isna(), None,
                                          inv["quadrant_workbook_lookup"] == inv["quadrant_rule"])
    inv["quadrant_consistent"] = inv["quadrant_consistent"].astype("object")
    # duplicates: the same device listed twice, or different devices at an identical coordinate pair
    inv["n_rows_for_device"] = inv.groupby("device_id")["device_id"].transform("size")
    inv["flag_duplicate_device_row"] = inv["n_rows_for_device"] > 1
    key = inv["latitude_src"].astype(str) + "|" + inv["longitude_src"].astype(str)
    size = key.groupby(key).transform("size")
    inv["n_devices_same_coordinates"] = size.where(inv["flag_coordinates_valid"], 0).astype(int)
    inv["flag_shared_coordinates"] = inv["n_devices_same_coordinates"] > 1
    codes = {k: i + 1 for i, k in enumerate(sorted({k for k, s in zip(key, size) if s > 1}))}
    inv["shared_coordinate_group"] = [codes.get(k) if s > 1 else None for k, s in zip(key, size)]
    inv["shared_coordinate_group"] = inv["shared_coordinate_group"].astype("object")
    return inv.reset_index(drop=True)


# ----------------------------------------------------------------------------- record level
def _int_series(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse integer-looking text. Returns (int values as float with NaN, mask of 'present but not an integer')."""
    present = s.notna()
    ok = present & s.fillna("").str.match(_INT)
    vals = pd.to_numeric(s.where(ok), errors="coerce")
    return vals, present & ~ok


def build_audit(data: pd.DataFrame, inventory: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """All worksheet rows (including blank rows), original text, normalised timestamps and flags."""
    a = data.rename(columns=SRC_COLS).copy()
    src = list(SRC_COLS.values())
    a["row_kind"] = np.where(a[src].isna().all(axis=1), "blank_row", "record")
    rec = a["row_kind"] == "record"

    y, y_bad = _int_series(a["src_year"])
    mo, mo_bad = _int_series(a["src_month"])
    d, d_bad = _int_series(a["src_day"])
    h, h_bad = _int_series(a["src_hour"])
    issue = pd.Series("", index=a.index, dtype="object")
    for name, vals, bad in (("year", y, y_bad), ("month", mo, mo_bad), ("day", d, d_bad), ("hour", h, h_bad)):
        issue = issue.where(issue != "", np.where(rec & vals.isna() & ~bad, f"{name}_missing",
                                                  np.where(rec & bad, f"{name}_not_integer", "")))
    hour_range_bad = rec & h.notna() & ~h.between(cfg.hour_label_min, cfg.hour_label_max)
    issue = issue.where(issue != "", np.where(hour_range_bad, "hour_out_of_range", ""))
    midnight = pd.to_datetime(pd.DataFrame({"year": y, "month": mo, "day": d}), errors="coerce")
    issue = issue.where(issue != "", np.where(rec & midnight.isna(), "invalid_calendar_date", ""))
    a["time_issue"] = issue
    a["flag_time_invalid"] = rec & (issue != "")
    t_ok = rec & (issue == "")

    # label instant and interval start
    label_naive = midnight + pd.to_timedelta(h, unit="h") + pd.Timedelta(hours=cfg.extra_shift_hours)
    label_src = label_naive.where(t_ok).dt.tz_localize(cfg.source_timezone, ambiguous="raise", nonexistent="raise")
    a["ts_label_utc"] = label_src.dt.tz_convert("UTC")
    start = a["ts_label_utc"] - (pd.Timedelta(hours=1) if cfg.hour_convention == "hour_ending" else pd.Timedelta(0))
    a["ts_start_utc"] = start
    a["ts_start_local"] = start.dt.tz_convert(LOCAL_TZ)
    a["ts_end_utc"] = start + pd.Timedelta(hours=1)
    a["hour_label"] = h.where(t_ok).astype("Int64")

    # device and PM2.5
    dev = a["src_device_id"].str.strip()
    a["device_id"] = dev
    known = set(inventory["device_id"])
    a["flag_device_missing"] = rec & dev.isna()
    a["flag_unknown_device"] = rec & dev.notna() & ~dev.isin(known)
    pm = pd.to_numeric(a["src_pm25"], errors="coerce")
    a["pm25_ugm3"] = pm.where(np.isfinite(pm))
    a["flag_pm25_missing"] = rec & a["src_pm25"].isna()
    a["flag_pm25_nonnumeric"] = rec & a["src_pm25"].notna() & a["pm25_ugm3"].isna()
    a["flag_pm25_nonpositive"] = a["pm25_ugm3"] <= 0
    a["flag_high_value"] = a["pm25_ugm3"] > cfg.high_value_ugm3

    # duplicates of (device, interval) among usable records
    usable = t_ok & dev.notna() & a["pm25_ugm3"].notna()
    key = dev.where(usable) + "|" + a["ts_start_utc"].dt.strftime("%Y-%m-%dT%H").where(usable)
    a["dup_group_size"] = key.groupby(key).transform("size").where(key.notna(), 1).astype(int)
    a["flag_duplicate_device_hour"] = a["dup_group_size"] > 1
    nun = a["pm25_ugm3"].where(usable).groupby(key).transform("nunique")
    a["flag_duplicate_conflict"] = a["flag_duplicate_device_hour"] & (nun > 1)
    order = pd.Series(np.arange(len(a)), index=a.index)
    first = order.where(usable).groupby(key).transform("min")
    a["is_primary_valid"] = (usable & ~a["flag_unknown_device"] & ~a["flag_duplicate_conflict"]
                             & ((~a["flag_duplicate_device_hour"]) | (order == first)))

    q = np.select(
        [a["row_kind"] == "blank_row", a["flag_device_missing"], a["flag_time_invalid"], a["flag_pm25_missing"],
         a["flag_pm25_nonnumeric"], a["flag_unknown_device"], a["flag_duplicate_conflict"]],
        ["BLANK_ROW", "DEVICE_MISSING", "TIME_INVALID", "PM_MISSING", "PM_NONNUMERIC", "UNKNOWN_DEVICE", "DUP_CONFLICT"],
        default="VALID",
    )
    a["quality_flag"] = pd.Series(q, index=a.index).astype(str)
    a["processing_version"] = PROCESSING_VERSION
    return a


def add_neighbor_flags(audit: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """For primary valid rows: median of the OTHER sensors in the same interval and an outlier candidate flag.

    A candidate is a reading above ``neighbor_ratio`` x that median when at least ``neighbor_min_others``
    other sensors report. It is only a flag: nothing is discarded."""
    a = audit
    n = len(a)
    med = np.full(n, np.nan)
    cnt = np.zeros(n, dtype=int)
    idx = np.flatnonzero(a["is_primary_valid"].to_numpy())
    tkey = a["ts_start_utc"].astype("int64").to_numpy()  # UTC instants as integers (fast grouping)
    sub = pd.DataFrame({"i": idx, "t": tkey[idx], "v": a["pm25_ugm3"].to_numpy()[idx]})
    for _, g in sub.groupby("t", sort=False):
        v = g["v"].to_numpy()
        ii = g["i"].to_numpy()
        m = len(v)
        cnt[ii] = m - 1
        if m < 2:
            continue
        if m - 1 >= cfg.neighbor_min_others:
            for k in range(m):
                med[ii[k]] = np.median(np.delete(v, k))
    a["n_neighbors"] = cnt
    a["neighbor_median_ugm3"] = med
    with np.errstate(divide="ignore", invalid="ignore"):
        a["neighbor_ratio"] = a["pm25_ugm3"] / a["neighbor_median_ugm3"]
    a["flag_neighbor_outlier"] = (
        a["is_primary_valid"] & (a["n_neighbors"] >= cfg.neighbor_min_others) & (a["neighbor_ratio"] > cfg.neighbor_ratio)
    )
    a["flag_neighbor_outlier"] = a["flag_neighbor_outlier"].fillna(False).astype(bool)
    return a


HOURLY_COLUMNS = [
    "device_id", "ts_start_local", "ts_start_utc", "ts_label_utc", "hour_label", "pm25_ugm3", "flag_high_value",
    "flag_neighbor_outlier", "neighbor_median_ugm3", "neighbor_ratio", "n_neighbors", "source_excel_row",
    "processing_version",
]


def build_hourly(audit: pd.DataFrame) -> pd.DataFrame:
    """Primary analysis table: one row per (device, interval) with a valid reading. No gap filling."""
    h = audit[audit["is_primary_valid"]].copy()
    if "flag_neighbor_outlier" not in h.columns:  # neighbour comparison not run (e.g. sensitivity variants)
        h["flag_neighbor_outlier"], h["neighbor_median_ugm3"], h["neighbor_ratio"], h["n_neighbors"] = False, np.nan, np.nan, 0
    h["source_excel_row"] = h["excel_row"]
    h = h.sort_values(["device_id", "ts_start_utc"]).reset_index(drop=True)
    h["hour_label"] = h["hour_label"].astype("int64")
    return h[HOURLY_COLUMNS]


# ----------------------------------------------------------------------------- aggregates
def _local_date(h: pd.DataFrame) -> pd.Series:
    return h["ts_start_local"].dt.tz_localize(None).dt.normalize()


def build_daily(hourly: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """Daily means per device. A day is the 24 intervals that START on the local (Bishkek) calendar date.
    The mean exists only with at least ``daily_min_valid_hours`` valid hours."""
    h = hourly.copy()
    h["date_local"] = _local_date(h)
    g = h.groupby(["device_id", "date_local"])["pm25_ugm3"]
    d = g.agg(n_valid_hours="size", pm25_mean_all_valid="mean", pm25_min="min", pm25_max="max").reset_index()
    d["meets_min_hours"] = d["n_valid_hours"] >= cfg.daily_min_valid_hours
    d["pm25_daily_mean_ugm3"] = d["pm25_mean_all_valid"].where(d["meets_min_hours"])
    d["n_high_value_hours"] = h.groupby(["device_id", "date_local"])["flag_high_value"].sum().to_numpy()
    d["n_neighbor_outlier_hours"] = h.groupby(["device_id", "date_local"])["flag_neighbor_outlier"].sum().to_numpy()
    d["min_valid_hours_required"] = cfg.daily_min_valid_hours
    return d[["device_id", "date_local", "n_valid_hours", "meets_min_hours", "pm25_daily_mean_ugm3", "pm25_mean_all_valid",
              "pm25_min", "pm25_max", "n_high_value_hours", "n_neighbor_outlier_hours", "min_valid_hours_required"]]


def build_monthly(daily: pd.DataFrame, hourly: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """Monthly mean of valid daily means, only when at least ``monthly_min_day_fraction`` of the eligible days
    have a valid daily mean. Eligible days = calendar days of that month inside the dataset's own date range."""
    if daily.empty:
        return pd.DataFrame()
    first, last = _local_date(hourly).min(), _local_date(hourly).max()
    days = pd.date_range(first, last, freq="D")
    eligible = pd.Series(days.to_period("M")).value_counts().to_dict()
    dd = daily.copy()
    dd["month"] = dd["date_local"].dt.to_period("M")
    valid_days = dd[dd["meets_min_hours"]]
    nvd = valid_days.groupby(["device_id", "month"]).size().rename("n_valid_days")
    mean = valid_days.groupby(["device_id", "month"])["pm25_daily_mean_ugm3"].mean().rename("mean_of_daily_means")
    nh = dd.groupby(["device_id", "month"])["n_valid_hours"].sum().rename("n_valid_hours")
    m = pd.concat([nh, nvd, mean], axis=1).reset_index()
    m["n_valid_days"] = m["n_valid_days"].fillna(0).astype(int)
    m["eligible_days"] = m["month"].map(eligible).astype(int)
    m["valid_day_fraction"] = m["n_valid_days"] / m["eligible_days"]
    m["meets_threshold"] = m["valid_day_fraction"] >= cfg.monthly_min_day_fraction
    m["pm25_monthly_mean_ugm3"] = m["mean_of_daily_means"].where(m["meets_threshold"])
    m["year_month"] = m["month"].astype(str)
    m["min_day_fraction_required"] = cfg.monthly_min_day_fraction
    return m[["device_id", "year_month", "n_valid_hours", "n_valid_days", "eligible_days", "valid_day_fraction",
              "meets_threshold", "pm25_monthly_mean_ugm3", "min_day_fraction_required"]].sort_values(
        ["device_id", "year_month"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- device statistics
def _longest_gap_hours(starts: pd.Series) -> tuple[int, int]:
    t = np.sort(starts.dropna().unique())
    if len(t) < 2:
        return 0, 0
    d = np.diff(t) / np.timedelta64(1, "h")
    gaps = d[d > 1] - 1
    return (int(gaps.max()) if len(gaps) else 0), int((gaps >= 24).sum())


def spearman(x, y) -> float:
    """Spearman rank correlation (Pearson on average ranks); no SciPy needed."""
    rx = pd.Series(np.asarray(x, dtype=float)).rank()
    ry = pd.Series(np.asarray(y, dtype=float)).rank()
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(rx.corr(ry))


def monthly_drift(monthly: pd.DataFrame, cfg: ClarityConfig) -> pd.DataFrame:
    """Ratio of each device's monthly mean to the network median that month (exploratory, not a fault test)."""
    m = monthly[monthly["pm25_monthly_mean_ugm3"].notna()].copy()
    net = m.groupby("year_month")["pm25_monthly_mean_ugm3"].median().rename("network_median")
    m = m.join(net, on="year_month")
    m["ratio_to_network"] = m["pm25_monthly_mean_ugm3"] / m["network_median"]
    recs = []
    for dev, g in m.groupby("device_id"):
        g = g.sort_values("year_month")
        r = g["ratio_to_network"].to_numpy()
        rho = spearman(r, np.arange(len(r))) if len(r) >= 3 else np.nan
        rng = float(r.max() / r.min()) if len(r) else np.nan
        flag = bool(len(r) >= cfg.drift_min_months and abs(rho) >= cfg.drift_min_abs_rho and rng >= cfg.drift_min_range_ratio)
        recs.append({"device_id": dev, "n_months": len(r), "ratio_min": float(r.min()), "ratio_max": float(r.max()),
                     "ratio_range_factor": rng, "spearman_rho_vs_month": float(rho), "log_ratio_sd": float(np.std(np.log(r))),
                     "flag_drift_candidate": flag})
    return pd.DataFrame(recs, columns=["device_id", "n_months", "ratio_min", "ratio_max", "ratio_range_factor",
                                       "spearman_rho_vs_month", "log_ratio_sd", "flag_drift_candidate"])


def device_coverage(audit: pd.DataFrame, hourly: pd.DataFrame, monthly: pd.DataFrame, inventory: pd.DataFrame,
                    cfg: ClarityConfig) -> pd.DataFrame:
    """Per-device counts, coverage, missing hours, gaps, summary statistics, flags and drift."""
    t0, t1 = hourly["ts_start_utc"].min(), hourly["ts_start_utc"].max()
    expected = int((t1 - t0) / pd.Timedelta(hours=1)) + 1
    rec = audit[audit["row_kind"] == "record"]
    tot = rec.groupby("device_id").size().rename("total_records")
    rows = []
    for dev, g in hourly.groupby("device_id"):
        v = g["pm25_ugm3"]
        own = int((g["ts_start_utc"].max() - g["ts_start_utc"].min()) / pd.Timedelta(hours=1)) + 1
        gap, n24 = _longest_gap_hours(g["ts_start_utc"])
        rows.append({
            "device_id": dev, "valid_readings": int(len(g)), "first_interval_start_utc": g["ts_start_utc"].min(),
            "last_interval_start_utc": g["ts_start_utc"].max(), "expected_hours_network_window": expected,
            "coverage_pct_network_window": 100 * len(g) / expected, "missing_hours_network_window": expected - len(g),
            "own_span_hours": own, "coverage_pct_own_span": 100 * len(g) / own, "missing_hours_own_span": own - len(g),
            "longest_gap_hours": gap, "n_gaps_24h_or_more": n24, "pm25_mean": v.mean(), "pm25_median": v.median(),
            "pm25_p95": v.quantile(0.95), "pm25_max": v.max(), "n_high_value": int(g["flag_high_value"].sum()),
            "n_neighbor_outlier": int(g["flag_neighbor_outlier"].sum()),
        })
    cov = pd.DataFrame(rows)
    cov = cov.merge(tot, left_on="device_id", right_index=True, how="left")
    cov["total_records"] = cov["total_records"].fillna(0).astype(int)
    cov["invalid_or_excluded_records"] = cov["total_records"] - cov["valid_readings"]
    med = cov["coverage_pct_network_window"].median()
    cov["flag_low_coverage"] = cov["coverage_pct_network_window"] < cfg.low_coverage_pct
    cov["flag_coverage_below_network_median_by_5pts"] = cov["coverage_pct_network_window"] < med - 5
    cov = cov.merge(monthly_drift(monthly, cfg), on="device_id", how="left")
    cov["flag_drift_candidate"] = cov["flag_drift_candidate"].fillna(False).astype(bool)
    return cov.sort_values("device_id").reset_index(drop=True)


def finish_inventory(inventory: pd.DataFrame, audit: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    """Add reading counts and the status ``no_data_in_file`` / ``has_data_in_file`` to the inventory."""
    inv = inventory.copy()
    rec = audit[audit["row_kind"] == "record"]
    n_rec = rec.groupby("device_id").size()
    n_val = hourly.groupby("device_id").size()
    inv["n_records_in_file"] = inv["device_id"].map(n_rec).fillna(0).astype(int)
    inv["n_valid_readings"] = inv["device_id"].map(n_val).fillna(0).astype(int)
    inv["status"] = np.where(inv["n_records_in_file"] > 0, "has_data_in_file", "no_data_in_file")
    inv["first_interval_start_utc"] = inv["device_id"].map(hourly.groupby("device_id")["ts_start_utc"].min())
    inv["last_interval_start_utc"] = inv["device_id"].map(hourly.groupby("device_id")["ts_start_utc"].max())
    return inv
