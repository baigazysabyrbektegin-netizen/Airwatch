"""Historical PM2.5 measurements for ONE station at a time, on that station's own timestamps."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from app.services import stations as st
from app.services.data_store import LOCAL_TZ, DataStore
from app.services.errors import BadQuery

INTERVALS = ["hourly", "daily", "monthly"]
MIN_DAILY_HOURS = 18
MIN_MONTHLY_FRACTION = 0.75
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_time(text: str | None, name: str, end_of_day: bool = False) -> pd.Timestamp | None:
    """ISO date or datetime. Without a time zone the value is Asia/Bishkek local time. A date-only 'end' means the whole day."""
    if text is None or text == "":
        return None
    try:
        t = pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise BadQuery(f"{name} must be an ISO date or datetime such as 2021-03-01 or 2021-03-01T12:00 (got {text!r}).") from exc
    if pd.isna(t):
        raise BadQuery(f"{name} is not a valid date/time ({text!r}).")
    if t.tzinfo is None:
        t = t.tz_localize(LOCAL_TZ)
    t = t.tz_convert(LOCAL_TZ)
    if end_of_day and _DATE_ONLY.match(text.strip()):
        t = t + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return t


def _range(start: str | None, end: str | None):
    s, e = parse_time(start, "start"), parse_time(end, "end", end_of_day=True)
    if s is not None and e is not None and s > e:
        raise BadQuery("start must not be later than end.")
    return s, e


# ------------------------------------------------------------------ Embassy derived tables (from the processed hourly table)
def embassy_daily(store: DataStore) -> pd.DataFrame:
    def build():
        e = store.embassy_hourly()
        valid = e[e["quality_flag"] == "VALID"]
        first, last = e["ts_local"].min().normalize().tz_localize(None), e["ts_local"].max().normalize().tz_localize(None)
        days = pd.date_range(first, last, freq="D")
        g = valid.groupby(valid["ts_local"].dt.tz_localize(None).dt.normalize())["pm25_model_ugm3"].agg(["size", "mean"])
        d = pd.DataFrame(index=days).join(g).rename(columns={"size": "n_valid_hours", "mean": "mean_all"})
        d["n_valid_hours"] = d["n_valid_hours"].fillna(0).astype(int)
        d["pm25_daily_mean_ugm3"] = d["mean_all"].where(d["n_valid_hours"] >= MIN_DAILY_HOURS)
        d.index.name = "date_local"
        return d.reset_index()

    return store.cached("embassy_daily", build)


def embassy_monthly(store: DataStore) -> pd.DataFrame:
    def build():
        d = embassy_daily(store)
        d = d.assign(month=d["date_local"].dt.to_period("M"))
        g = d.groupby("month").agg(eligible_days=("date_local", "size"), n_valid_days=("pm25_daily_mean_ugm3", "count"), mean=("pm25_daily_mean_ugm3", "mean"))
        g["valid_day_fraction"] = g["n_valid_days"] / g["eligible_days"]
        g["meets_threshold"] = g["valid_day_fraction"] >= MIN_MONTHLY_FRACTION
        g["pm25_monthly_mean_ugm3"] = g["mean"].where(g["meets_threshold"])
        g.index = g.index.astype(str)
        g.index.name = "year_month"
        return g.reset_index()

    return store.cached("embassy_monthly", build)


# ------------------------------------------------------------------ record builders
def _num(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def _clarity_hourly(store, sid, s, e):
    h = store.clarity_hourly()
    d = h[h["device_id"] == sid]
    if s is not None:
        d = d[d["ts_start_local"] >= s]
    if e is not None:
        d = d[d["ts_start_local"] <= e]
    return d.sort_values("ts_start_local"), lambda sl: [{
        "timestamp": st.iso(r.ts_start_local), "interval_end": st.iso(r.ts_start_local + pd.Timedelta(hours=1)), "pm25_ugm3": _num(r.pm25_ugm3),
        "pm25_valid_ugm3": _num(r.pm25_ugm3), "quality_flag": "VALID",
        "flags": [f for f, on in (("HIGH_VALUE_OVER_500", r.flag_high_value), ("NEIGHBOR_OUTLIER_CANDIDATE", r.flag_neighbor_outlier)) if on],
        "detail": {"neighbor_median_ugm3": _num(r.neighbor_median_ugm3), "n_neighbors": int(r.n_neighbors)}} for r in sl.itertuples(index=False)]


def _embassy_hourly(store, s, e, include_gaps):
    h = store.embassy_hourly()
    d = h if include_gaps else h[h["row_present"]]
    if s is not None:
        d = d[d["ts_local"] >= s]
    if e is not None:
        d = d[d["ts_local"] <= e]

    def build(sl):
        out = []
        for r in sl.itertuples(index=False):
            flags = [f for f, on in (("NEGATIVE_NOISE", r.flag_neg_noise), ("HIGH_VALUE_OVER_500", r.flag_high_value), ("LOCAL_DROP", r.flag_local_drop),
                                     ("LOCAL_SPIKE", r.flag_local_spike), ("DUPLICATE_TIMESTAMP", r.flag_duplicate_ts), ("DUPLICATE_CONFLICT", r.flag_duplicate_conflict)) if on]
            out.append({"timestamp": st.iso(r.ts_local), "interval_end": None, "pm25_ugm3": _num(r.pm25_raw_ugm3), "pm25_valid_ugm3": _num(r.pm25_model_ugm3),
                        "quality_flag": r.quality_flag, "flags": flags, "detail": {"qc_name_original": r.qc_name_original if isinstance(r.qc_name_original, str) else None}})
        return out

    return d.sort_values("ts_local"), build


def _daily(df, kind, s, e):
    d = df
    day = d["date_local"]
    if s is not None:
        d = d[day >= s.tz_localize(None).normalize()]
    if e is not None:
        d = d[day <= e.tz_localize(None).normalize()]
    d = d.sort_values("date_local")

    def build(sl):
        out = []
        for r in sl.itertuples(index=False):
            ok = int(r.n_valid_hours) >= MIN_DAILY_HOURS
            out.append({"timestamp": pd.Timestamp(r.date_local).date().isoformat(), "interval_end": None, "pm25_ugm3": _num(r.pm25_daily_mean_ugm3), "pm25_valid_ugm3": _num(r.pm25_daily_mean_ugm3),
                        "quality_flag": "COMPLETE_DAY" if ok else "INCOMPLETE_DAY", "flags": [] if ok else [f"UNDER_{MIN_DAILY_HOURS}_VALID_HOURS"],
                        "detail": {"n_valid_hours": int(r.n_valid_hours)}})
        return out

    return d, build


def _monthly(df, s, e):
    d = df
    if s is not None:
        d = d[d["year_month"] >= s.strftime("%Y-%m")]
    if e is not None:
        d = d[d["year_month"] <= e.strftime("%Y-%m")]
    d = d.sort_values("year_month")

    def build(sl):
        out = []
        for r in sl.itertuples(index=False):
            out.append({"timestamp": r.year_month, "interval_end": None, "pm25_ugm3": _num(r.pm25_monthly_mean_ugm3), "pm25_valid_ugm3": _num(r.pm25_monthly_mean_ugm3),
                        "quality_flag": "MEETS_75PCT_DAY_RULE" if bool(r.meets_threshold) else "BELOW_75PCT_DAY_RULE",
                        "flags": [] if bool(r.meets_threshold) else ["UNDER_75PCT_OF_ELIGIBLE_DAYS"],
                        "detail": {"n_valid_days": int(r.n_valid_days), "eligible_days": int(r.eligible_days)}})
        return out

    return d, build


def query(store: DataStore, station_id: str, start: str | None, end: str | None, interval: str, limit: int, offset: int, include_gaps: bool) -> dict:
    station = st.get_station(store, station_id)
    if not station["data_available"]:
        raise BadQuery(f"Station {station['id']} has no readings in the processed data (status {station['status']}).")
    s, e = _range(start, end)
    is_embassy = station["network"] == "us-embassy"
    if interval == "hourly":
        data, build = _embassy_hourly(store, s, e, include_gaps) if is_embassy else _clarity_hourly(store, station["id"], s, e)
        unit_note = "hourly values"
    elif interval == "daily":
        cd = store.clarity_daily()
        base = embassy_daily(store) if is_embassy else cd[cd["device_id"] == station["id"]]
        data, build = _daily(base, "daily", s, e)
        unit_note = f"daily means; a day needs at least {MIN_DAILY_HOURS} valid hours, otherwise the mean is null (never 0)"
    else:
        cm = store.clarity_monthly()
        base = embassy_monthly(store) if is_embassy else cm[cm["device_id"] == station["id"]]
        data, build = _monthly(base, s, e)
        unit_note = f"monthly means of valid daily means; a month needs at least {int(MIN_MONTHLY_FRACTION * 100)}% of its eligible days, otherwise null"
    total = int(len(data))
    page = data.iloc[offset: offset + limit]
    recs = build(page)
    notes = [unit_note, station["timestamp_convention"], st.ALIGNMENT_NOTE,
             "Values are historical measurements, not current conditions. PM2.5 in µg/m³ as supplied; missing values are null, never zero."]
    if is_embassy:
        notes.append("pm25_ugm3 is the original measurement (may be negative: flagged NEGATIVE_NOISE); pm25_valid_ugm3 is the model-ready value (valid hours only, negatives clipped to 0).")
        if recs:
            first, last = (_local(pd.Timestamp(recs[i]["timestamp"])) for i in (0, -1))
            if first <= st.REGIME_END and last >= st.REGIME_START:
                notes.append(st.REGIME_NOTE)
    else:
        notes.append("Clarity sensors are low-cost; calibration is reported by the source paper but not independently verified.")
    return {
        "station": {"id": station["id"], "name": station["name"], "network": station["network"], "data_kind": station["data_kind"],
                    "location_status": station["location_status"]},
        "interval": interval, "unit": "µg/m³", "timestamp_basis": station["timestamp_convention"], "alignment_note": st.ALIGNMENT_NOTE,
        "query": {"start": st.iso(s), "end": st.iso(e), "limit": limit, "offset": offset, "include_gaps": include_gaps},
        "total": total, "count": len(recs), "truncated": total > offset + limit, "records": recs, "notes": notes,
    }


def _local(t: pd.Timestamp) -> pd.Timestamp:
    return t.tz_convert(LOCAL_TZ) if t.tzinfo else t.tz_localize(LOCAL_TZ)
