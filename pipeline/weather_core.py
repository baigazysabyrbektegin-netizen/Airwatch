"""ERA5 reanalysis weather from the Open-Meteo Historical Weather API: request, cache, parse and validate.

ERA5 is reanalysis (a model-assimilated reconstruction of past weather on a ~0.25 degree grid), not a local weather
station and not a forecast. The request names the ERA5 model explicitly (``models=era5``): the API default
("best match") silently mixes ERA5, ERA5-Land and IFS, and ERA5-Land does not provide wind, pressure, precipitation
or boundary-layer height. Nothing here substitutes another dataset or interpolates missing values.

The network call is injectable (``http_get``) so the tests never touch the internet.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
LOCAL_TZ = "Asia/Bishkek"
MODEL = "era5"
DEFAULT_LAT, DEFAULT_LON = 42.917, 74.510

# name -> (expected unit string returned by the API, plausible range for the sanity check, aggregation hint, label)
VARIABLES: dict[str, dict] = {
    "temperature_2m": {"unit": "°C", "range": (-60.0, 60.0), "label": "Air temperature at 2 m"},
    "relative_humidity_2m": {"unit": "%", "range": (0.0, 100.0), "label": "Relative humidity at 2 m"},
    "wind_speed_10m": {"unit": "m/s", "range": (0.0, 80.0), "label": "Wind speed at 10 m"},
    "wind_direction_10m": {"unit": "°", "range": (0.0, 360.0), "label": "Wind direction at 10 m (from)"},
    "surface_pressure": {"unit": "hPa", "range": (500.0, 1100.0), "label": "Surface pressure"},
    "precipitation": {"unit": "mm", "range": (0.0, 200.0), "label": "Precipitation (sum of the preceding hour)"},
    "boundary_layer_height": {"unit": "m", "range": (0.0, 6000.0), "label": "Boundary layer height"},
}
REQUEST_EXTRA = {"timezone": "UTC", "wind_speed_unit": "ms", "temperature_unit": "celsius", "precipitation_unit": "mm", "timeformat": "iso8601", "models": MODEL}

HttpGet = Callable[[str], tuple[int, bytes, dict]]


class WeatherError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ssl_context():
    """Verified TLS. certifi's CA bundle is preferred when installed (it is a dependency of httpx): this machine's
    Python/Windows trust store rejected the server's chain as 'expired'. Verification is never disabled."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def default_http_get(url: str, timeout: float = 120.0) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "airwatch-research-pipeline", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:  # the API answers errors with a JSON body worth keeping
        return exc.code, exc.read(), dict(exc.headers or {})


@dataclass(frozen=True)
class Request:
    latitude: float
    longitude: float
    start_date: str
    end_date: str
    variables: tuple[str, ...] = tuple(VARIABLES)

    def params(self) -> dict:
        return {"latitude": self.latitude, "longitude": self.longitude, "start_date": self.start_date, "end_date": self.end_date,
                "hourly": ",".join(self.variables), **REQUEST_EXTRA}

    def url(self) -> str:
        return ENDPOINT + "?" + urllib.parse.urlencode(self.params(), safe=",")

    def stem(self) -> str:
        return f"openmeteo_era5_{self.latitude:.3f}_{self.longitude:.3f}_{self.start_date}_{self.end_date}"


@dataclass
class Fetched:
    raw_path: Path
    request_path: Path
    downloaded: bool
    sha256: str
    n_bytes: int
    retrieved_at_utc: str
    http_status: int


def fetch(req: Request, raw_dir: Path, http_get: HttpGet = default_http_get, refresh: bool = False) -> Fetched:
    """Download once and cache. An existing, intact cache file is reused (no network) unless ``refresh`` is set."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path, meta_path = raw_dir / f"{req.stem()}.json", raw_dir / f"{req.stem()}.request.json"
    if raw_path.is_file() and meta_path.is_file() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if digest != meta.get("response_sha256"):
            raise WeatherError(f"Cached response {raw_path.name} does not match its recorded checksum; refusing to use it. Re-run with --refresh to download again.")
        return Fetched(raw_path, meta_path, False, digest, raw_path.stat().st_size, meta["retrieved_at_utc"], int(meta.get("http_status", 200)))

    status, body, headers = http_get(req.url())
    if status != 200:
        reason = ""
        try:
            reason = json.loads(body).get("reason", "")
        except Exception:
            pass
        raise WeatherError(f"Open-Meteo answered HTTP {status}{': ' + reason if reason else ''}. Nothing was cached.")
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise WeatherError("Open-Meteo returned a response that is not JSON; nothing was cached.") from exc
    if not isinstance(doc, dict) or "hourly" not in doc:
        raise WeatherError("The response has no 'hourly' block; nothing was cached.")
    if raw_path.exists() and not refresh:
        raise WeatherError(f"{raw_path} exists without its request record; refusing to overwrite. Use --refresh to replace it deliberately.")
    retrieved = utc_now()
    digest = hashlib.sha256(body).hexdigest()
    raw_path.write_bytes(body)  # the original bytes, unmodified
    meta = {"url": req.url(), "params": req.params(), "retrieved_at_utc": retrieved, "http_status": status, "response_sha256": digest, "response_bytes": len(body),
            "response_headers": {k: v for k, v in headers.items() if k.lower() in ("content-type", "date", "content-length", "x-ratelimit-remaining")}}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return Fetched(raw_path, meta_path, True, digest, len(body), retrieved, status)


# ---------------------------------------------------------------------------------------------------- parse
def parse(doc: dict, variables: tuple[str, ...] = tuple(VARIABLES)) -> tuple[pd.DataFrame, dict]:
    """Original response -> one row per API timestamp (kept exactly as delivered, duplicates included).

    Timestamps are UTC instants (``timezone=UTC``); ``ts_local`` is the same instant in Asia/Bishkek. Missing values stay NaN."""
    hourly, units = doc.get("hourly"), doc.get("hourly_units", {})
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise WeatherError("The response has no hourly time axis.")
    n = len(hourly["time"])
    bad_units = {v: units.get(v) for v in variables if v in hourly and units.get(v) != VARIABLES[v]["unit"]}
    if bad_units:
        raise WeatherError(f"Unexpected units {bad_units}; expected { {v: VARIABLES[v]['unit'] for v in bad_units} }. Values were not converted silently.")
    ts = pd.to_datetime(pd.Series(hourly["time"]), format="%Y-%m-%dT%H:%M", errors="coerce")
    if ts.isna().any():
        raise WeatherError(f"{int(ts.isna().sum())} timestamps could not be parsed.")
    df = pd.DataFrame({"ts_utc": ts.dt.tz_localize("UTC")})
    df["ts_local"] = df["ts_utc"].dt.tz_convert(LOCAL_TZ)
    missing_vars = [v for v in variables if v not in hourly]
    for v in variables:
        if v in hourly:
            if len(hourly[v]) != n:
                raise WeatherError(f"Variable {v} has {len(hourly[v])} values for {n} timestamps.")
            df[v] = pd.to_numeric(pd.Series(hourly[v], dtype="object"), errors="coerce").astype("float64")
        else:
            df[v] = np.nan
    info = {
        "requested_latitude": None, "returned_latitude": doc.get("latitude"), "returned_longitude": doc.get("longitude"), "elevation_m": doc.get("elevation"),
        "utc_offset_seconds": doc.get("utc_offset_seconds"), "timezone": doc.get("timezone"), "units": {v: units.get(v) for v in variables if v in hourly},
        "variables_missing_from_response": missing_vars, "api_generationtime_ms": doc.get("generationtime_ms"),
    }
    return df, info


# ---------------------------------------------------------------------------------------------------- validate
def validate(df: pd.DataFrame, variables: tuple[str, ...] = tuple(VARIABLES)) -> dict:
    """Checks only; the data are never altered, interpolated or dropped here."""
    dup = int(df["ts_utc"].duplicated().sum())
    out: dict = {"rows": int(len(df)), "duplicate_timestamps": dup, "monotonic_increasing": bool(df["ts_utc"].is_monotonic_increasing)}
    if len(df):
        first, last = df["ts_utc"].min(), df["ts_utc"].max()
        expected = pd.date_range(first, last, freq="h")
        present = pd.DatetimeIndex(df["ts_utc"].unique())
        missing = expected.difference(present)
        out.update({
            "first_utc": first.isoformat(), "last_utc": last.isoformat(), "first_local": first.tz_convert(LOCAL_TZ).isoformat(), "last_local": last.tz_convert(LOCAL_TZ).isoformat(),
            "expected_hours": int(len(expected)), "distinct_hours_present": int(len(present)), "missing_hours": int(len(missing)),
            "hourly_coverage_pct": round(100.0 * len(present) / len(expected), 3), "missing_hour_examples": [t.isoformat() for t in missing[:5]],
        })
    per_var = {}
    for v in variables:
        s = df[v]
        lo, hi = VARIABLES[v]["range"]
        valid = s.dropna()
        miss = s.isna()
        run_id = (miss != miss.shift()).cumsum()
        runs = df.loc[miss].groupby(run_id[miss])["ts_utc"].agg(["min", "max", "size"]) if miss.any() else None
        per_var[v] = {"missing_runs": int(len(runs)) if runs is not None else 0, "longest_missing_run_hours": int(runs["size"].max()) if runs is not None else 0,
                      "first_missing_utc": runs["min"].min().isoformat() if runs is not None else None, "last_missing_utc": runs["max"].max().isoformat() if runs is not None else None,"unit": VARIABLES[v]["unit"], "n_valid": int(valid.size), "n_missing": int(s.isna().sum()), "pct_valid_of_rows": round(100.0 * valid.size / max(len(s), 1), 3),
                      "n_outside_plausible_range": int(((valid < lo) | (valid > hi)).sum()), "plausible_range": [lo, hi],
                      "min": float(valid.min()) if valid.size else None, "max": float(valid.max()) if valid.size else None, "mean": float(valid.mean()) if valid.size else None}
    out["variables"] = per_var
    return out
