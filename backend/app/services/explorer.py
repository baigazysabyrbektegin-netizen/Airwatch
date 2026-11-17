"""Data & Weather Explorer: historical PM2.5 for ONE station, optional ERA5 weather alongside, summaries and comparisons.

Safeguards (they matter more than the statistics):
- A response contains one station on its own timestamps. Embassy and Clarity are never merged into one series.
- No timestamp is shifted to improve agreement. Pairing PM2.5 with weather uses a stated reference time plus an explicit,
  user-chosen offset (default 0) that is echoed back in the response. The Embassy/Clarity 1-hour offset stays unresolved.
- Only VALID observations enter statistics. Excluded, suspect, flagged-but-included and imputed counts are reported separately.
- Missing values are null, never 0, and are never interpolated. Gaps are returned as null records so charts can show them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import measurements as ms
from app.services import stations as st
from app.services import weather as wx
from app.services.data_store import LOCAL_TZ, DataStore
from app.services.errors import BadQuery, DataUnavailable

SCATTER_VARS = ["temperature_2m", "wind_speed_10m", "relative_humidity_2m", "precipitation"]
MIN_PAIRS = 30
MIN_DAYS_FOR_DIFFERENCE = 10
HOURLY_MAX_DAYS = 120
EXPLORER_INTERVALS = ["hourly", "daily", "monthly"]
SEASON_OF_MONTH = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM", 6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
SEASON_LABEL = {"DJF": "Winter (Dec–Feb)", "MAM": "Spring (Mar–May)", "JJA": "Summer (Jun–Aug)", "SON": "Autumn (Sep–Nov)"}
SEASON_ORDER = ["DJF", "MAM", "JJA", "SON"]
MIN_SEASON_FRACTION = 0.75
ASSOCIATION_NOTE = ("Correlations describe statistical association between PM2.5 and ERA5 weather. They do not show cause and effect, and weather does not "
                    "explain all PM2.5 variation (emissions, season, heating and measurement behaviour also matter).")


def _loc(text) -> pd.Timestamp:
    """An ISO string with a UTC offset -> a Timestamp in the Asia/Bishkek zone (so it can be compared with the window)."""
    return pd.Timestamp(text).tz_convert(LOCAL_TZ)


def _num(x):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        return None
    return float(x)


# ---------------------------------------------------------------------------------------------------- station & window
def resolve_station(store: DataStore, source: str | None, station_id: str | None) -> tuple[dict, str]:
    src = (source or "").strip().lower() or None
    if src not in (None, "embassy", "clarity"):
        raise BadQuery("source must be 'embassy' or 'clarity'.", ["embassy", "clarity"])
    if not station_id:
        if src == "clarity":
            raise BadQuery("station_id is required when source=clarity (a Clarity device id).")
        station_id = st.EMBASSY_ID
    station = st.get_station(store, station_id)
    net = "embassy" if station["network"] == "us-embassy" else "clarity"
    if src and src != net:
        raise BadQuery(f"Station {station['id']} belongs to the {net} network, not {src}.")
    if not station["data_available"]:
        raise BadQuery(f"Station {station['id']} has no readings in the processed data (status {station['status']}).")
    return station, net


def _span_days(station: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    p = station["data_period"]
    first = pd.Timestamp(p["start"]).tz_convert(LOCAL_TZ).tz_localize(None).normalize()
    last = pd.Timestamp(p["end"]).tz_convert(LOCAL_TZ).tz_localize(None).normalize()
    return first, last


def window(station: dict, start_date: str | None, end_date: str | None) -> dict:
    """Requested dates clipped to the station's own data period (local calendar days)."""
    s, e = wx.date_range(start_date, end_date)
    first, last = _span_days(station)
    lo, hi = max(s, first) if s is not None else first, min(e, last) if e is not None else last
    return {"requested": {"start_date": start_date or None, "end_date": end_date or None}, "lo": lo, "hi": hi, "empty": bool(lo > hi), "first": first, "last": last,
            "lo_ts": lo.tz_localize(LOCAL_TZ), "hi_ts": (hi + pd.Timedelta(days=1)).tz_localize(LOCAL_TZ) - pd.Timedelta(microseconds=1)}


def _window_out(w: dict) -> dict:
    return {**w["requested"], "effective_start": w["lo"].date().isoformat(), "effective_end": w["hi"].date().isoformat(),
            "station_data_period": {"first": w["first"].date().isoformat(), "last": w["last"].date().isoformat()}}


# ---------------------------------------------------------------------------------------------------- PM2.5 tables
def daily_pm(store: DataStore, station: dict, net: str, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """Every local day in [lo, hi]: n_valid_hours and the daily mean (null below 18 valid hours; never 0)."""
    if net == "embassy":
        d = ms.embassy_daily(store)[["date_local", "n_valid_hours", "pm25_daily_mean_ugm3"]]
    else:
        cd = store.clarity_daily()
        d = cd[cd["device_id"] == station["id"]][["date_local", "n_valid_hours", "pm25_daily_mean_ugm3"]]
    d = d.set_index("date_local").reindex(pd.date_range(lo, hi, freq="D"))
    d["n_valid_hours"] = d["n_valid_hours"].fillna(0).astype(int)
    d.index.name = "date_local"
    return d.reset_index()


def monthly_from_daily(d: pd.DataFrame) -> pd.DataFrame:
    """Monthly mean of valid daily means; needs 75% of the month's days inside the window to be valid, otherwise null."""
    ym = d["date_local"].dt.to_period("M").astype(str)
    g = d.groupby(ym)["pm25_daily_mean_ugm3"]
    out = pd.DataFrame({"eligible_days": d.groupby(ym).size(), "n_valid_days": g.count(), "mean": g.mean()})
    out["meets_threshold"] = out["n_valid_days"] / out["eligible_days"] >= ms.MIN_MONTHLY_FRACTION
    out["pm25"] = out["mean"].where(out["meets_threshold"])
    out.index.name = "year_month"
    return out.reset_index()


def _hourly_records(store: DataStore, station: dict, net: str, w: dict) -> list[dict]:
    if net == "embassy":
        data, build = ms._embassy_hourly(store, w["lo_ts"], w["hi_ts"], True)
        return build(data)
    data, build = ms._clarity_hourly(store, station["id"], w["lo_ts"], w["hi_ts"])
    recs = {r["timestamp"]: r for r in build(data)}
    if not recs:
        return []
    first, last = max(w["lo_ts"], _loc(station["data_period"]["start"])), min(w["hi_ts"], _loc(station["data_period"]["end"]))
    out = []
    for t in pd.date_range(first.floor("h"), last.floor("h"), freq="h"):
        k = st.iso(t)
        out.append(recs.get(k) or {"timestamp": k, "interval_end": st.iso(t + pd.Timedelta(hours=1)), "pm25_ugm3": None, "pm25_valid_ugm3": None,
                                   "quality_flag": "NO_READING", "flags": [], "detail": None})
    return out


def pm_records(store: DataStore, station: dict, net: str, interval: str, w: dict) -> list[dict]:
    if interval == "hourly":
        return _hourly_records(store, station, net, w)
    d = daily_pm(store, station, net, w["lo"], w["hi"])
    if interval == "daily":
        out = []
        for r in d.itertuples(index=False):
            ok = r.n_valid_hours >= ms.MIN_DAILY_HOURS
            out.append({"timestamp": pd.Timestamp(r.date_local).date().isoformat(), "interval_end": None, "pm25_ugm3": _num(r.pm25_daily_mean_ugm3),
                        "pm25_valid_ugm3": _num(r.pm25_daily_mean_ugm3), "quality_flag": "COMPLETE_DAY" if ok else ("NO_READINGS" if r.n_valid_hours == 0 else "INCOMPLETE_DAY"),
                        "flags": [] if ok else [f"UNDER_{ms.MIN_DAILY_HOURS}_VALID_HOURS"], "detail": {"n_valid_hours": int(r.n_valid_hours)}})
        return out
    m = monthly_from_daily(d)
    return [{"timestamp": r.year_month, "interval_end": None, "pm25_ugm3": _num(r.pm25), "pm25_valid_ugm3": _num(r.pm25),
             "quality_flag": "MEETS_75PCT_DAY_RULE" if r.meets_threshold else "BELOW_75PCT_DAY_RULE", "flags": [] if r.meets_threshold else ["UNDER_75PCT_OF_ELIGIBLE_DAYS"],
             "detail": {"n_valid_days": int(r.n_valid_days), "eligible_days": int(r.eligible_days)}} for r in m.itertuples(index=False)]


# ---------------------------------------------------------------------------------------------------- weather pairing
def _weather_for(store: DataStore, net: str, interval: str, records: list[dict], names: list[str], offset_hours: int, w: dict) -> pd.DataFrame:
    keys = [r["timestamp"] for r in records]
    if interval == "hourly":
        # Reference time: Embassy = the label as given; Clarity = the END of the hour (the label), matching ERA5's preceding-hour precipitation.
        ref = [pd.Timestamp(r["interval_end"] if net == "clarity" and r.get("interval_end") else r["timestamp"]) + pd.Timedelta(hours=offset_hours) for r in records]
        h = wx.hourly_table(store).set_index("ts_local")[names]
        return h.reindex(pd.DatetimeIndex(ref)).reset_index(drop=True)
    if interval == "daily":
        d = wx.daily_table(store).set_index("date_local")[names]
        return d.reindex(pd.to_datetime(keys)).reset_index(drop=True)
    m = wx.monthly_in_range(store, w["lo"], w["hi"]).set_index("year_month")[names]
    return m.reindex(keys).reset_index(drop=True)


def alignment(net: str, interval: str, offset: int, include_weather: bool) -> dict:
    if interval == "hourly":
        basis = ("Clarity: the label at the END of each hour (interval start + 1 h, the assumed hour-ending convention) is matched to the ERA5 timestamp of the same instant."
                 if net == "clarity" else "Embassy: the 'Date (LT)' label as given is matched to the ERA5 timestamp of the same local time (start/end convention is undocumented).")
    elif interval == "daily":
        basis = "Local calendar days (Asia/Bishkek) are matched to local-day ERA5 aggregates."
    else:
        basis = "Calendar months (Asia/Bishkek) are matched to ERA5 monthly aggregates built from the same days."
    return {"weather_included": include_weather, "pairing_basis": basis, "weather_offset_hours": offset, "offset_applies": interval == "hourly",
            "statement": (f"User-selected weather offset: {offset:+d} h (0 = no shift). " if interval == "hourly" else "") +
                         "No offset was chosen to improve correlation. The Clarity/Embassy 1-hour label offset is unresolved and the two networks are never merged.",
            "weather_timestamps": "ERA5 hourly timestamps are UTC instants shown in Asia/Bishkek (UTC+6); precipitation is the sum of the preceding hour."}


def _corr(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    if x.size < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None, None
    pear = float(np.corrcoef(x, y)[0, 1])
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    sp = float(np.corrcoef(rx, ry)[0, 1]) if np.std(rx) and np.std(ry) else None
    return pear, sp


def relationships(series: list[dict], names: list[str]) -> dict:
    out = {}
    for v in names:
        pairs = [(r["pm25"], r["weather"][v]) for r in series if r["pm25"] is not None and r["weather"].get(v) is not None]
        x = np.array([p[0] for p in pairs], dtype=float)
        y = np.array([p[1] for p in pairs], dtype=float)
        ok = len(pairs) >= MIN_PAIRS
        pear, sp = _corr(x, y) if ok else (None, None)
        out[v] = {"n_pairs": len(pairs), "enough_pairs": ok, "min_pairs_required": MIN_PAIRS, "pearson_r": pear, "spearman_rho": sp,
                  "reason": None if ok else f"Only {len(pairs)} valid PM2.5/weather pairs; at least {MIN_PAIRS} are needed before a correlation is shown."}
    return out


# ---------------------------------------------------------------------------------------------------- warnings
def quality_warnings(station: dict, net: str, w: dict, flagged: dict | None = None) -> list[dict]:
    out = []

    def add(code, severity, message):
        out.append({"code": code, "severity": severity, "message": message})

    if net == "embassy":
        if w["lo"] <= st.REGIME_END.tz_localize(None).normalize() and w["hi"] >= st.REGIME_START.tz_localize(None).normalize():
            add("embassy_regime_2023_2024", "warning", st.REGIME_NOTE + " Do not treat 2023–2024 as an unquestioned validation period.")
        add("embassy_location_approximate", "info", "The Embassy monitor's coordinates are approximate (an OpenStreetMap building point); they are not used in any calculation here.")
        add("embassy_timestamp_convention", "info", st.EMBASSY_TIMESTAMP_NOTE)
    else:
        add("clarity_not_calibrated_verified", "warning", "Clarity sensors are low-cost. The source paper reports periodic calibration, but it was not independently verified; values may be biased.")
        add("clarity_timestamp_convention", "info", st.CLARITY_TIMESTAMP_NOTE)
        if flagged and (flagged.get("high_value_over_500") or flagged.get("neighbor_outlier_candidate")):
            add("clarity_flagged_values_included", "warning", f"{flagged.get('high_value_over_500', 0)} reading(s) over 500 µg/m³ and {flagged.get('neighbor_outlier_candidate', 0)} neighbour-outlier candidate(s) "
                                                          "are flagged but INCLUDED (the cleaning rules flag them without removing them).")
    add("clock_alignment_unresolved", "info", st.ALIGNMENT_NOTE)
    add("historical_not_current", "info", "All values are historical (Embassy 2019–2024, Clarity 2021). They are not current conditions.")
    return out


def _weather_warnings(store: DataStore, names: list[str], w: dict) -> list[dict]:
    out = []
    h = wx.hourly_table(store)
    wfirst, wlast = h["ts_local"].min().tz_localize(None).normalize(), h["ts_local"].max().tz_localize(None).normalize()
    if w["lo"] < wfirst or w["hi"] > wlast:
        out.append({"code": "weather_period_shorter", "severity": "warning", "message": f"ERA5 weather covers {wfirst.date()} to {wlast.date()}; weather is null outside it."})
    sl = wx._slice_hourly(store, w["lo"], w["hi"])
    for v in names:
        miss = int(sl[v].isna().sum())
        if len(sl) and miss:
            out.append({"code": f"weather_missing_{v}", "severity": "warning", "message": f"{wx.LABELS[v]}: {miss:,} of {len(sl):,} hourly values in this range are missing in the ERA5 response (kept missing, not filled)."})
    out.append({"code": "era5_not_local", "severity": "info", "message": wx.REANALYSIS_NOTE})
    return out


# ---------------------------------------------------------------------------------------------------- explorer
def explorer(store: DataStore, source: str | None, station_id: str | None, start_date: str | None, end_date: str | None, interval: str, include_weather: bool,
             weather_offset_hours: int, variables: str | None) -> dict:
    if interval not in EXPLORER_INTERVALS:
        raise BadQuery(f"interval must be one of {EXPLORER_INTERVALS}.", EXPLORER_INTERVALS)
    if weather_offset_hours != 0 and interval != "hourly":
        raise BadQuery("weather_offset_hours only applies to interval=hourly; daily and monthly values are matched by calendar day or month.")
    if abs(weather_offset_hours) > 12:
        raise BadQuery("weather_offset_hours must be between -12 and 12.")
    names = wx.parse_variables(variables)
    station, net = resolve_station(store, source, station_id)
    w = window(station, start_date, end_date)
    base = {"station": {"id": station["id"], "name": station["name"], "network": station["network"], "data_kind": station["data_kind"], "location_status": station["location_status"],
                        "timestamp_convention": station["timestamp_convention"]},
            "source": net, "interval": interval, "unit": "µg/m³", "query": _window_out(w), "alignment": alignment(net, interval, weather_offset_hours, include_weather)}
    if w["empty"]:
        return {**base, "empty": True, "empty_reason": f"The selected dates do not overlap this station's data period ({w['first'].date()} to {w['last'].date()}).", "count": 0, "series": [],
                "relationships": {}, "weather": None, "warnings": quality_warnings(station, net, {"lo": w["first"], "hi": w["last"]}), "notes": []}
    if interval == "hourly" and (w["hi"] - w["lo"]).days + 1 > HOURLY_MAX_DAYS:
        raise BadQuery(f"Hourly data is limited to {HOURLY_MAX_DAYS} days per request to keep the response small. Choose a shorter range or the daily/monthly interval.")
    recs = pm_records(store, station, net, interval, w)
    warnings = quality_warnings(station, net, w, _flag_counts(store, station, net, w))
    wdf = None
    weather = {"included": False}
    if include_weather:
        try:
            wdf = _weather_for(store, net, interval, recs, names, weather_offset_hours, w)
            weather = {"included": True, "variables": wx.variable_meta(store, names), "provenance": wx.provenance(store), "notes": [wx.REANALYSIS_NOTE, wx.TIME_NOTE]}
            warnings += _weather_warnings(store, names, w)
        except DataUnavailable as exc:
            weather = {"included": False, "unavailable": True}
            warnings.append({"code": "weather_unavailable", "severity": "warning", "message": f"ERA5 weather is not available ({exc.file}: {exc.reason}). {exc.hint}"})
    series = []
    for i, r in enumerate(recs):
        series.append({"timestamp": r["timestamp"], "pm25": r["pm25_valid_ugm3"], "pm25_raw": r["pm25_ugm3"], "quality_flag": r["quality_flag"], "flags": r["flags"],
                       "detail": r.get("detail"), "weather": {v: _num(wdf.iloc[i][v]) for v in names} if wdf is not None else {}})
    n_valid = sum(1 for r in series if r["pm25"] is not None)
    return {**base, "empty": n_valid == 0, "empty_reason": None if n_valid else "No valid PM2.5 observations in the selected range.", "count": len(series), "n_valid": n_valid,
            "series": series, "weather": weather,
            "relationships": {"variables": relationships(series, [v for v in SCATTER_VARS if v in names]) if wdf is not None else {}, "basis": f"{interval} values",
                              "note": ASSOCIATION_NOTE},
            "warnings": warnings, "notes": [f"Source: {station['data_kind']}. Values are PM2.5 in µg/m³; missing values are null, never zero.",
                                            ("Only VALID hours are used; negative readings were clipped to 0 by the cleaning rules (original values are in pm25_raw)." if net == "embassy" else
                                             "Readings are as supplied by the low-cost sensor; flagged values are included."), st.ALIGNMENT_NOTE]}


# ---------------------------------------------------------------------------------------------------- summary
def _flag_counts(store: DataStore, station: dict, net: str, w: dict) -> dict:
    if net == "clarity":
        h = store.clarity_hourly()
        d = h[(h["device_id"] == station["id"]) & (h["ts_start_local"] >= w["lo_ts"]) & (h["ts_start_local"] <= w["hi_ts"])]
        return {"high_value_over_500": int(d["flag_high_value"].sum()), "neighbor_outlier_candidate": int(d["flag_neighbor_outlier"].sum())}
    return {}


def _stats(values: pd.Series) -> dict:
    v = values.dropna().to_numpy(dtype=float)
    if v.size == 0:
        return {k: None for k in ("mean", "median", "min", "max", "std", "p05", "p25", "p75", "p95", "p99")}
    q = np.quantile(v, [0.05, 0.25, 0.75, 0.95, 0.99])
    return {"mean": float(v.mean()), "median": float(np.median(v)), "min": float(v.min()), "max": float(v.max()), "std": float(v.std(ddof=1)) if v.size > 1 else None,
            "p05": float(q[0]), "p25": float(q[1]), "p75": float(q[2]), "p95": float(q[3]), "p99": float(q[4])}


def _hourly_basis(store: DataStore, station: dict, net: str, w: dict) -> tuple[pd.Series, dict, int]:
    """Valid hourly values in the window, the category counts, and the number of hours expected."""
    if net == "embassy":
        e = store.embassy_hourly()
        sl = e[(e["ts_local"] >= w["lo_ts"]) & (e["ts_local"] <= w["hi_ts"])]
        valid = sl[sl["quality_flag"] == "VALID"]
        qc = sl["quality_flag"].value_counts()
        counts = {
            "expected_hours": int(len(sl)), "valid": int(len(valid)),
            "excluded": {"hours_without_source_row": int(qc.get("NO_ROW", 0)), "provider_invalid": int(qc.get("INVALID", 0)), "missing_value": int(qc.get("MISSING_VALUE", 0)),
                         "duplicate_conflict": int(qc.get("DUP_CONFLICT", 0))},
            "suspect": {"excluded_suspect_hours": int(qc.get("SUSPECT", 0)),
                        "flagged_but_included": {"negative_noise": int(valid["flag_neg_noise"].sum()), "high_value_over_500": int(valid["flag_high_value"].sum()),
                                                "local_drop": int(valid["flag_local_drop"].sum()), "local_spike": int(valid["flag_local_spike"].sum())}},
            "imputed": {"hours_with_feature_interpolation": int(sl["is_imputed"].sum()), "used_in_statistics": False,
                        "note": "Short-gap interpolation exists only in a separate model-feature column; it is never used here."},
            "negative_clipped_to_zero": int((valid["pm25_raw_ugm3"] < 0).sum()),
        }
        counts["excluded"]["total"] = sum(counts["excluded"].values())
        return valid["pm25_model_ugm3"], counts, int(len(sl))
    h = store.clarity_hourly()
    sl = h[(h["device_id"] == station["id"]) & (h["ts_start_local"] >= w["lo_ts"]) & (h["ts_start_local"] <= w["hi_ts"])]
    first, last = max(w["lo_ts"], _loc(station["data_period"]["start"])), min(w["hi_ts"], _loc(station["data_period"]["end"]))
    expected = int((last.floor("h") - first.floor("h")) / pd.Timedelta(hours=1)) + 1 if last >= first else 0
    counts = {
        "expected_hours": expected, "valid": int(len(sl)),
        "excluded": {"hours_without_reading": max(expected - int(len(sl)), 0), "total": max(expected - int(len(sl)), 0)},
        "suspect": {"excluded_suspect_hours": 0, "flagged_but_included": {"high_value_over_500": int(sl["flag_high_value"].sum()), "neighbor_outlier_candidate": int(sl["flag_neighbor_outlier"].sum())}},
        "imputed": {"hours_with_feature_interpolation": 0, "used_in_statistics": False, "note": "The Clarity hourly table contains no imputed values."},
        "negative_clipped_to_zero": 0,
    }
    return sl["pm25_ugm3"], counts, expected


def _season_tables(d: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    d = d.copy()
    d["month"] = d["date_local"].dt.month
    d["season"] = d["month"].map(SEASON_OF_MONTH)
    d["season_year"] = np.where(d["month"].isin([1, 2]), d["date_local"].dt.year - 1, d["date_local"].dt.year)
    overall = []
    for s in SEASON_ORDER:
        g = d[d["season"] == s]
        v = g["pm25_daily_mean_ugm3"].dropna()
        overall.append({"season": s, "label": SEASON_LABEL[s], "eligible_days": int(len(g)), "n_valid_days": int(v.size), "mean_of_daily_means": _num(v.mean()) if v.size else None,
                        "median_of_daily_means": _num(v.median()) if v.size else None, "p95_of_daily_means": _num(v.quantile(0.95)) if v.size else None})
    by_year = []
    for (sy, s), g in d.groupby(["season_year", "season"]):
        v = g["pm25_daily_mean_ugm3"].dropna()
        ok = len(g) > 0 and v.size / len(g) >= MIN_SEASON_FRACTION
        by_year.append({"season_year": int(sy), "season": s, "label": f"{SEASON_LABEL[s]} {int(sy)}" + (f"/{int(sy) + 1}" if s == "DJF" else ""), "eligible_days": int(len(g)), "n_valid_days": int(v.size),
                        "meets_75pct_rule": bool(ok), "mean_of_daily_means": _num(v.mean()) if ok else None})
    by_year.sort(key=lambda r: (r["season_year"], SEASON_ORDER.index(r["season"])))
    return overall, by_year


def summarize(store: DataStore, station: dict, net: str, w: dict) -> dict:
    values, counts, expected = _hourly_basis(store, station, net, w)
    d = daily_pm(store, station, net, w["lo"], w["hi"])
    dv = d["pm25_daily_mean_ugm3"].dropna()
    m = monthly_from_daily(d)
    clim = []
    ok = m[m["meets_threshold"]].copy()
    ok["cal"] = ok["year_month"].str[5:7].astype(int)
    for cm in range(1, 13):
        g = ok[ok["cal"] == cm]["pm25"]
        clim.append({"month": cm, "n_months": int(g.size), "mean": _num(g.mean()) if g.size else None, "min": _num(g.min()) if g.size else None, "max": _num(g.max()) if g.size else None})
    overall, by_year = _season_tables(d)
    n_valid = int(counts["valid"])
    return {
        "station": {"id": station["id"], "name": station["name"], "network": station["network"], "data_kind": station["data_kind"], "location_status": station["location_status"]},
        "source": net, "unit": "µg/m³", "query": _window_out(w), "empty": n_valid == 0,
        "empty_reason": None if n_valid else "No valid PM2.5 observations in the selected range.",
        "stats": _stats(values), "stats_basis": "valid hourly values" + (" (model-ready: negatives clipped to 0)" if net == "embassy" else ""),
        "n_valid": n_valid, "expected_hours": expected, "coverage_pct": round(100.0 * n_valid / expected, 2) if expected else None,
        "coverage_definition": "valid hours / hours between the first and last day inside the selected range and the station's own data period",
        "observations": counts,
        "daily": {"n_valid_days": int(dv.size), "n_days": int(len(d)), "min_valid_hours_per_day": ms.MIN_DAILY_HOURS, **{f"{k}_of_daily_means": v for k, v in _stats(dv).items() if k in ("mean", "median", "p95", "max", "min")}},
        "monthly": [{"year_month": r.year_month, "mean": _num(r.pm25), "n_valid_days": int(r.n_valid_days), "eligible_days": int(r.eligible_days), "meets_75pct_rule": bool(r.meets_threshold)}
                    for r in m.itertuples(index=False)],
        "climatology": clim, "seasonal": overall, "seasonal_by_year": by_year,
        "notes": ["Statistics use VALID observations only. Months need 75% of their days valid and days need 18 valid hours; otherwise the value is null (never 0).",
                  "Seasons: winter = Dec–Feb (the winter of year Y is Dec Y to Feb Y+1). Seasonal means are means of valid daily means.", st.ALIGNMENT_NOTE],
    }


def summary(store: DataStore, source: str | None, station_id: str | None, start_date: str | None, end_date: str | None) -> dict:
    station, net = resolve_station(store, source, station_id)
    w = window(station, start_date, end_date)
    if w["empty"]:
        return {"station": {"id": station["id"], "name": station["name"], "network": station["network"]}, "source": net, "query": _window_out(w), "empty": True,
                "empty_reason": f"The selected dates do not overlap this station's data period ({w['first'].date()} to {w['last'].date()}).",
                "warnings": quality_warnings(station, net, {"lo": w["first"], "hi": w["last"]})}
    out = summarize(store, station, net, w)
    out["warnings"] = quality_warnings(station, net, w, _flag_counts(store, station, net, w))
    return out


# ---------------------------------------------------------------------------------------------------- comparison
def _weather_means(store: DataStore, w: dict) -> dict:
    try:
        d = wx.daily_table(store)
    except DataUnavailable:
        return {}
    d = d[(d["date_local"] >= w["lo"]) & (d["date_local"] <= w["hi"])]
    out = {}
    for v in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation"):
        s = d[v].dropna()
        out[v] = {"mean_of_daily_values": _num(s.mean()) if s.size else None, "n_days": int(s.size), "unit": "mm/day" if v == "precipitation" else None}
    return out


def _side(store: DataStore, station_id: str | None, start: str | None, end: str | None, interval: str, label: str) -> dict:
    station, net = resolve_station(store, None, station_id)
    w = window(station, start, end)
    if w["empty"]:
        return {"label": label, "station": {"id": station["id"], "name": station["name"], "network": station["network"]}, "source": net, "query": _window_out(w), "empty": True,
                "empty_reason": f"Period {label}: the selected dates do not overlap this station's data period ({w['first'].date()} to {w['last'].date()}).", "series": [], "_w": w, "_station": station, "_net": net}
    sm = summarize(store, station, net, w)
    recs = pm_records(store, station, net, interval, w)
    series = [{"index": i, "timestamp": r["timestamp"], "pm25": r["pm25_valid_ugm3"]} for i, r in enumerate(recs)]
    return {"label": label, "station": sm["station"], "source": net, "query": sm["query"], "empty": sm["empty"], "empty_reason": sm["empty_reason"], "summary": sm, "series": series,
            "weather": _weather_means(store, w), "_w": w, "_station": station, "_net": net}


def _diff(a: float | None, b: float | None) -> dict:
    if a is None or b is None:
        return {"a": a, "b": b, "difference_b_minus_a": None, "percent_change": None}
    return {"a": a, "b": b, "difference_b_minus_a": b - a, "percent_change": (b - a) / a * 100.0 if a > 0 else None}


def compare(store: DataStore, station_a: str | None, start_a: str | None, end_a: str | None, station_b: str | None, start_b: str | None, end_b: str | None, interval: str) -> dict:
    if interval not in ("daily", "monthly"):
        raise BadQuery("Comparison supports interval=daily or monthly.", ["daily", "monthly"])
    if not (start_a and end_a and start_b and end_b):
        raise BadQuery("start_a, end_a, start_b and end_b are all required for a comparison.")
    a = _side(store, station_a, start_a, end_a, interval, "A")
    b = _side(store, station_b or station_a, start_b, end_b, interval, "B")
    warnings: list[dict] = []

    def add(code, sev, msg):
        warnings.append({"code": code, "severity": sev, "message": msg})

    for s in (a, b):
        if s["empty"]:
            add("empty_period", "warning", s["empty_reason"])
    diff = {"available": False, "reason": None}
    if not a["empty"] and not b["empty"]:
        sa, sb = a["summary"], b["summary"]
        same_station = a["station"]["id"] == b["station"]["id"]
        if a["source"] != b["source"]:
            add("different_networks", "warning", "The two sides come from different networks (Embassy vs Clarity). Their time labels are not confirmed to be synchronised and the instruments differ, "
                                                 "so the comparison is statistical only: no timestamp pairing is made.")
        elif not same_station:
            add("different_stations", "info", "Different stations are compared on their own timestamps; locations and coverage differ.")
        if same_station:
            wa, wb = a["_w"], b["_w"]
            if wa["lo"] <= wb["hi"] and wb["lo"] <= wa["hi"]:
                add("overlapping_periods", "warning", "The two periods overlap; the comparison is not between independent periods.")
        da, db = (a["_w"]["hi"] - a["_w"]["lo"]).days + 1, (b["_w"]["hi"] - b["_w"]["lo"]).days + 1
        if da != db:
            add("unequal_length", "info", f"Period A covers {da} days and period B {db} days.")
        months_a = {t.month for t in pd.date_range(a["_w"]["lo"], a["_w"]["hi"], freq="D")}
        months_b = {t.month for t in pd.date_range(b["_w"]["lo"], b["_w"]["hi"], freq="D")}
        if months_a != months_b:
            add("different_seasons", "warning", "The periods cover different calendar months, so differences may reflect seasonality rather than a change over time.")
        ca, cb = sa["coverage_pct"], sb["coverage_pct"]
        if ca is not None and cb is not None and abs(ca - cb) > 10:
            add("unequal_coverage", "warning", f"Data coverage differs ({ca:.0f}% vs {cb:.0f}% of hours); means rest on different amounts of data.")
        for lab, s in (("A", sa), ("B", sb)):
            if 0 < s["daily"]["n_valid_days"] < MIN_DAYS_FOR_DIFFERENCE:
                add("few_valid_days", "warning", f"Period {lab} has only {s['daily']['n_valid_days']} valid daily means.")
        if sa["daily"]["n_valid_days"] >= MIN_DAYS_FOR_DIFFERENCE and sb["daily"]["n_valid_days"] >= MIN_DAYS_FOR_DIFFERENCE:
            diff = {"available": True, "reason": None, "hourly_mean": _diff(sa["stats"]["mean"], sb["stats"]["mean"]), "median": _diff(sa["stats"]["median"], sb["stats"]["median"]),
                    "mean_of_daily_means": _diff(sa["daily"]["mean_of_daily_means"], sb["daily"]["mean_of_daily_means"]),
                    "p95": _diff(sa["stats"]["p95"], sb["stats"]["p95"]), "max": _diff(sa["stats"]["max"], sb["stats"]["max"]),
                    "weather": {v: _diff(a["weather"].get(v, {}).get("mean_of_daily_values"), b["weather"].get(v, {}).get("mean_of_daily_values")) for v in a["weather"]} if a["weather"] and b["weather"] else {}}
        else:
            diff["reason"] = f"A difference is shown only when both periods have at least {MIN_DAYS_FOR_DIFFERENCE} valid daily means."
    for lab, s in (("A", a), ("B", b)):
        if not s["empty"]:
            for q in quality_warnings(s["_station"], s["_net"], s["_w"], _flag_counts(store, s["_station"], s["_net"], s["_w"])):
                if q["severity"] == "warning":
                    warnings.append({**q, "code": f"period_{lab.lower()}_{q['code']}", "message": f"Period {lab}: {q['message']}"})
    for s in (a, b):
        s.pop("_w", None), s.pop("_station", None), s.pop("_net", None)
    return {"interval": interval, "unit": "µg/m³", "period_a": a, "period_b": b, "difference": diff, "warnings": warnings,
            "alignment": {"statement": "Series are aligned by position within each period (day 0, day 1, …), not by calendar date, so periods of different years can be overlaid. "
                                       "No timestamp is shifted.", "x_axis": "index = days (or months) since the start of each period"},
            "notes": ["A difference between periods is descriptive. It does not by itself show that anything changed or what caused it.",
                      st.ALIGNMENT_NOTE]}
