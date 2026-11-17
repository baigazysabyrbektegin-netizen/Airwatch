"""Historical ERA5 reanalysis weather (processed by pipeline.process_weather), with local-day aggregation.

ERA5 is reanalysis: a model-assimilated reconstruction of PAST weather on a ~0.25 degree grid. It is not a forecast, not a
local station record, and not real time. Missing values stay missing (null, never 0 and never interpolated).

Aggregation uses Asia/Bishkek local days so it lines up with the PM2.5 daily means:
- a day needs at least 18 valid hourly values of that variable, otherwise the day's value is null;
- a month needs valid daily values on at least 75% of its days, otherwise null;
- temperature, humidity, pressure, wind speed and boundary layer height are averaged; precipitation is summed;
- wind direction is a circular (vector) mean of unit vectors, because averaging angles is wrong (350 and 10 degrees).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from app.services.data_store import LOCAL_TZ, DataStore
from app.services.errors import BadQuery

VARIABLES = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "surface_pressure", "precipitation", "boundary_layer_height"]
LABELS = {
    "temperature_2m": "Air temperature at 2 m", "relative_humidity_2m": "Relative humidity at 2 m", "wind_speed_10m": "Wind speed at 10 m",
    "wind_direction_10m": "Wind direction at 10 m (direction the wind blows from)", "surface_pressure": "Surface pressure",
    "precipitation": "Precipitation", "boundary_layer_height": "Boundary layer height",
}
SUM_VARS = {"precipitation"}
DIRECTION_VAR = "wind_direction_10m"
INTERVALS = ["hourly", "daily", "monthly"]
MIN_DAILY_HOURS = 18
MIN_MONTHLY_FRACTION = 0.75
HOURLY_ROW_CAP = 20000
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REANALYSIS_NOTE = ("ERA5 reanalysis (via Open-Meteo): a model-based reconstruction of past weather on a ~0.25 degree (~25 km) grid. It is NOT a weather forecast, "
                   "NOT a local ground-station measurement and NOT real time.")
TIME_NOTE = ("Hourly timestamps are UTC instants converted to Asia/Bishkek (UTC+6). Precipitation at time T is the sum over the preceding hour (T-1h to T); "
             "the other variables are instantaneous values at T.")


def parse_date(text: str | None, name: str) -> pd.Timestamp | None:
    """YYYY-MM-DD (a calendar day in Asia/Bishkek). Anything else is rejected with an explanation."""
    if text is None or text == "":
        return None
    if not _DATE.match(text.strip()):
        raise BadQuery(f"{name} must be a calendar date like 2021-03-01 (got {text!r}).")
    try:
        return pd.Timestamp(text.strip())
    except ValueError as exc:
        raise BadQuery(f"{name} is not a valid date ({text!r}).") from exc


def date_range(start: str | None, end: str | None) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    s, e = parse_date(start, "start_date"), parse_date(end, "end_date")
    if s is not None and e is not None and s > e:
        raise BadQuery("start_date must not be later than end_date.")
    return s, e


def parse_variables(text: str | None) -> list[str]:
    if not text:
        return list(VARIABLES)
    names = [t.strip() for t in text.split(",") if t.strip()]
    bad = [n for n in names if n not in VARIABLES]
    if bad:
        raise BadQuery(f"Unknown weather variable(s): {', '.join(bad)}.", VARIABLES)
    return list(dict.fromkeys(names))


def _num(x):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        return None
    return float(x)


def circular_mean_deg(values: pd.Series) -> float:
    v = values.dropna().to_numpy(dtype=float)
    if v.size == 0:
        return np.nan
    rad = np.deg2rad(v)
    s, c = np.sin(rad).mean(), np.cos(rad).mean()
    if np.hypot(s, c) < 1e-9:  # directions cancel out: no meaningful mean direction
        return np.nan
    return float((np.rad2deg(np.arctan2(s, c)) + 360.0) % 360.0)


# ---------------------------------------------------------------------------------------------------- tables
def hourly_table(store: DataStore) -> pd.DataFrame:
    def build():
        d = store.weather_hourly().copy()
        d["ts_local"] = d["ts_local"].dt.tz_convert(LOCAL_TZ)
        return d.sort_values("ts_local").reset_index(drop=True)

    return store.cached("weather_hourly_sorted", build)


def daily_table(store: DataStore) -> pd.DataFrame:
    """One row per local day: <var> (null if under 18 valid hours) and n_<var> valid-hour counts."""
    def build():
        h = hourly_table(store)
        day = h["ts_local"].dt.tz_localize(None).dt.normalize()
        out = pd.DataFrame({"date_local": sorted(day.unique())}).set_index("date_local")
        for v in VARIABLES:
            g = h.groupby(day)[v]
            n = g.count()
            val = g.sum(min_count=1) if v in SUM_VARS else (g.apply(circular_mean_deg) if v == DIRECTION_VAR else g.mean())
            out[f"n_{v}"] = n.reindex(out.index).fillna(0).astype(int)
            out[v] = val.reindex(out.index).where(out[f"n_{v}"] >= MIN_DAILY_HOURS)
        return out.reset_index()

    return store.cached("weather_daily", build)


def monthly_table(store: DataStore) -> pd.DataFrame:
    def build():
        d = daily_table(store)
        ym = d["date_local"].dt.to_period("M").astype(str)
        out = pd.DataFrame({"year_month": sorted(ym.unique())}).set_index("year_month")
        eligible = d.groupby(ym).size()
        for v in VARIABLES:
            g = d.groupby(ym)[v]
            n = g.count()
            val = g.sum(min_count=1) if v in SUM_VARS else (g.apply(circular_mean_deg) if v == DIRECTION_VAR else g.mean())
            out[f"n_days_{v}"] = n.reindex(out.index).fillna(0).astype(int)
            out["eligible_days"] = eligible.reindex(out.index).astype(int)
            out[v] = val.reindex(out.index).where(out[f"n_days_{v}"] / out["eligible_days"] >= MIN_MONTHLY_FRACTION)
        return out.reset_index()

    return store.cached("weather_monthly", build)


# ---------------------------------------------------------------------------------------------------- provenance
def provenance(store: DataStore) -> dict:
    m = store.manifest_or_none("weather") or {}
    g, r, s = m.get("grid", {}), m.get("retrieval", {}), m.get("source", {})
    attr = (m.get("licence") or {}).get("attribution") or {}
    val = (m.get("validation") or {})
    return {
        "provider": s.get("provider", "Open-Meteo"), "model": s.get("model", "era5"), "data_kind": "reanalysis (modelled historical weather)", "is_forecast": False,
        "is_local_measurement": False, "spatial_resolution": s.get("spatial_resolution", "about 0.25 degrees (~25 km)"),
        "grid": {"requested_latitude": g.get("requested_latitude"), "requested_longitude": g.get("requested_longitude"), "returned_latitude": g.get("returned_latitude"),
                 "returned_longitude": g.get("returned_longitude"), "elevation_m": g.get("elevation_m")},
        "retrieved_at_utc": r.get("retrieved_at_utc"), "response_sha256": r.get("response_sha256"), "dataset_version": s.get("dataset_version", "not reported by the API"),
        "period": {"first_utc": val.get("first_utc"), "last_utc": val.get("last_utc"), "first_local": val.get("first_local"), "last_local": val.get("last_local")},
        "attribution": [a for a in (attr.get("open_meteo", "Weather data by Open-Meteo.com (CC BY 4.0)."), attr.get("copernicus", "")) if a],
        "licence": "CC BY 4.0 (Open-Meteo). The free API is for non-commercial use only.",
        "limitations": m.get("known_limitations", []),
    }


def variable_meta(store: DataStore, names: list[str]) -> dict:
    m = (store.manifest_or_none("weather") or {}).get("variables", {})
    return {v: {"label": LABELS[v], "unit": (m.get(v) or {}).get("unit") or {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "m/s", "wind_direction_10m": "°",
                                                                            "surface_pressure": "hPa", "precipitation": "mm", "boundary_layer_height": "m"}[v],
                "aggregation": {"hourly": "as delivered", "daily": "sum" if v in SUM_VARS else ("circular mean" if v == DIRECTION_VAR else "mean"),
                                "monthly": "sum of daily values" if v in SUM_VARS else ("circular mean of daily values" if v == DIRECTION_VAR else "mean of daily values")}}
            for v in names}


# ---------------------------------------------------------------------------------------------------- queries
def _slice_hourly(store: DataStore, s: pd.Timestamp | None, e: pd.Timestamp | None) -> pd.DataFrame:
    h = hourly_table(store)
    if s is not None:
        h = h[h["ts_local"] >= s.tz_localize(LOCAL_TZ)]
    if e is not None:
        h = h[h["ts_local"] < (e + pd.Timedelta(days=1)).tz_localize(LOCAL_TZ)]
    return h


def _table_for(store: DataStore, interval: str, s, e) -> tuple[pd.DataFrame, str]:
    if interval == "hourly":
        return _slice_hourly(store, s, e), "ts_local"
    if interval == "daily":
        d = daily_table(store)
        if s is not None:
            d = d[d["date_local"] >= s]
        if e is not None:
            d = d[d["date_local"] <= e]
        return d, "date_local"
    m = monthly_table(store)
    if s is not None:
        m = m[m["year_month"] >= s.strftime("%Y-%m")]
    if e is not None:
        m = m[m["year_month"] <= e.strftime("%Y-%m")]
    return m, "year_month"


def stamp(interval: str, value) -> str:
    if interval == "hourly":
        return pd.Timestamp(value).isoformat()
    if interval == "daily":
        return pd.Timestamp(value).date().isoformat()
    return str(value)


def query(store: DataStore, start_date: str | None, end_date: str | None, variables: str | None, interval: str, limit: int, offset: int) -> dict:
    if interval not in INTERVALS:
        raise BadQuery(f"interval must be one of {INTERVALS}.", INTERVALS)
    names = parse_variables(variables)
    s, e = date_range(start_date, end_date)
    table, key = _table_for(store, interval, s, e)
    total = int(len(table))
    page = table.iloc[offset: offset + limit]
    cols = [key, *names]
    if interval != "hourly":
        cols += [(f"n_{v}" if interval == "daily" else f"n_days_{v}") for v in names]
    records = []
    for row in page[cols].itertuples(index=False):
        rec = {"timestamp": stamp(interval, row[0])}
        for i, v in enumerate(names, start=1):
            rec[v] = _num(row[i])
        if interval != "hourly":
            rec["valid_counts"] = {v: int(row[1 + len(names) + i]) for i, v in enumerate(names)}
        records.append(rec)
    notes = [REANALYSIS_NOTE, TIME_NOTE, "Missing values are null; nothing is interpolated or filled."]
    if interval == "daily":
        notes.append(f"A daily value needs at least {MIN_DAILY_HOURS} valid hours of that variable; otherwise it is null. Precipitation is the daily sum; wind direction a circular mean.")
    if interval == "monthly":
        notes.append(f"A monthly value needs valid daily values on at least {int(MIN_MONTHLY_FRACTION * 100)}% of the month's days; otherwise it is null. Precipitation is the monthly total.")
    first = hourly_table(store)["ts_local"]
    return {
        "interval": interval, "variables": variable_meta(store, names), "query": {"start_date": start_date, "end_date": end_date, "limit": limit, "offset": offset},
        "available_period": {"first_local": first.min().isoformat(), "last_local": first.max().isoformat()},
        "total": total, "count": len(records), "truncated": total > offset + limit, "records": records, "provenance": provenance(store), "notes": notes,
    }


def monthly_in_range(store: DataStore, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """Monthly weather built only from the days inside [lo, hi] (naive local dates), with the same 75%-of-days rule.

    Days without weather data count as missing, so a month that is only partly covered by ERA5 is null rather than extrapolated."""
    d = daily_table(store).set_index("date_local").reindex(pd.date_range(lo, hi, freq="D"))
    ym = d.index.to_period("M").astype(str)
    out = pd.DataFrame({"year_month": sorted(set(ym))}).set_index("year_month")
    eligible = pd.Series(1, index=d.index).groupby(ym).sum()
    out["eligible_days"] = eligible.reindex(out.index).astype(int)
    for v in VARIABLES:
        g = d.groupby(ym)[v]
        n = g.count().reindex(out.index).fillna(0).astype(int)
        val = g.sum(min_count=1) if v in SUM_VARS else (g.apply(circular_mean_deg) if v == DIRECTION_VAR else g.mean())
        out[v] = val.reindex(out.index).where(n / out["eligible_days"] >= MIN_MONTHLY_FRACTION)
    return out.reset_index()
