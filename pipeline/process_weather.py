"""Phase 2: historical ERA5 weather for Bishkek via Open-Meteo -> raw cache, interim parse, processed table, report, manifest.

Run from the repository root with the backend virtual environment:

    python -m pipeline.process_weather                 # downloads once (cached afterwards)
    python -m pipeline.process_weather --refresh       # deliberately download again
    python -m pipeline.process_weather --allow-overwrite

Folders: raw response ``data/raw/weather/``; parsed (no cleaning) ``data/interim/weather/``;
processed ``data/processed/weather/``; provenance ``data/manifest/weather_processing.json``.
The API is called at most once per request definition: an intact cached file is reused.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow

from pipeline import io_utils as io
from pipeline import manifest as mf
from pipeline import weather_core as wc

REPO_ROOT = io.REPO_ROOT
EMBASSY_MANIFEST = REPO_ROOT / "data" / "manifest" / "embassy_processing.json"
PROCESSING_MANIFEST = REPO_ROOT / "data" / "manifest" / "weather_processing.json"
PROCESSING_VERSION = "weather-1.0.0"
SCRIPT = "pipeline/process_weather.py"
FALLBACK_PERIOD = ("2019-02-06", "2024-06-30")  # Embassy first/last local day, used only if its manifest is unavailable

DECISIONS = [
    "Dataset: ERA5 reanalysis requested explicitly with models=era5 through the Open-Meteo Historical Weather API. The API default ('best match') blends several models and is not used. ERA5-Land was tested and rejected because it returns no wind, pressure, precipitation or boundary-layer height; no other dataset is substituted.",
    "Time: requested with timezone=UTC so every timestamp is an unambiguous instant; Asia/Bishkek (UTC+6, no daylight saving) is derived from it. Rows are kept as delivered (ordering and duplicates preserved in the interim file).",
    "Units: wind speed requested in m/s (the API default is km/h). The returned unit strings are checked against the expected ones and the run fails rather than converting silently.",
    "Precipitation is the sum over the PRECEDING hour (so a row stamped T describes T-1h..T); the other variables are instantaneous values at T.",
    "Missing values stay missing. Nothing is interpolated, filled or clipped; out-of-range values are counted in the report but not altered.",
    "Duplicate timestamps: identical duplicates are collapsed to one row; conflicting duplicates would have their values set to missing and be counted (none occur unless the report says so).",
    "The ERA5 cell (about 0.25 degrees, ~25 km) is NOT a measurement at the requested point; the returned grid coordinates are recorded.",
]
LIMITATIONS = [
    "ERA5 is reanalysis (a model-assimilated reconstruction of past weather), not a forecast and not a local weather-station record. At ~25 km resolution it cannot resolve Bishkek's valley, urban heat island or local drainage winds.",
    "The returned grid cell is centred away from the requested point (see manifest 'grid'); Open-Meteo also adjusts some variables to the requested elevation using a 90 m terrain model.",
    "Boundary layer height is a model diagnostic with coarse vertical resolution; treat it as indicative, especially in stable winter conditions.",
    "Weather data are associated with PM2.5 only statistically. No causal claim is supported by this dataset.",
    "The free Open-Meteo API is for NON-COMMERCIAL use only; a commercial deployment needs a paid API key. Data are CC BY 4.0 and the attribution below must accompany any display.",
]
ATTRIBUTION = {
    "open_meteo": "Weather data by Open-Meteo.com (https://open-meteo.com/), CC BY 4.0.",
    "copernicus": "Generated using Copernicus Climate Change Service information (ERA5, Hersbach et al., 2023). Neither the European Commission nor ECMWF is responsible for any use of this information.",
    "citation": "Zippenfenig, P. (2023). Open-Meteo.com Weather API. Zenodo. https://doi.org/10.5281/ZENODO.7970649",
}


def embassy_period(manifest_path: Path = EMBASSY_MANIFEST) -> tuple[str, str, str]:
    """UTC start/end dates that cover the Embassy record, from its manifest (with the source of the dates)."""
    try:
        d = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["dataset"]
        s = pd.Timestamp(d["first_timestamp_local"]).tz_convert("UTC").date().isoformat()
        e = pd.Timestamp(d["last_timestamp_local"]).tz_convert("UTC").date().isoformat()
        return s, e, "data/manifest/embassy_processing.json"
    except Exception:
        return FALLBACK_PERIOD[0], FALLBACK_PERIOD[1], "fallback constants (Embassy manifest unavailable)"


def dedupe(df: pd.DataFrame, variables: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    """Collapse exact duplicate timestamps; blank the values of conflicting ones. Sorted by time."""
    stats = {"exact_duplicates_collapsed": 0, "conflicting_duplicate_timestamps": 0}
    if not df["ts_utc"].duplicated().any():
        return df.sort_values("ts_utc").reset_index(drop=True), stats
    rows = []
    for _, g in df.groupby("ts_utc", sort=True):
        if len(g) == 1:
            rows.append(g.iloc[0])
            continue
        vals = g[list(variables)]
        same = all(vals[c].nunique(dropna=False) <= 1 for c in variables)
        r = g.iloc[0].copy()
        if same:
            stats["exact_duplicates_collapsed"] += len(g) - 1
        else:
            stats["conflicting_duplicate_timestamps"] += 1
            r[list(variables)] = np.nan
        rows.append(r)
    return pd.DataFrame(rows).reset_index(drop=True), stats


def report_text(manifest: dict) -> str:
    v, c, g, r = manifest["validation"], manifest["coverage"], manifest["grid"], manifest["retrieval"]
    lines = [
        "# ERA5 weather validation report (Open-Meteo Historical Weather API)", "",
        f"Produced by `{SCRIPT}` ({PROCESSING_VERSION}); deterministic given the cached response (no run timestamp).", "",
        "## 1. Source",
        f"- Endpoint: {manifest['source']['endpoint']} with `models=era5` (ERA5 reanalysis; NOT a forecast, NOT a local station).",
        f"- Request: lat {g['requested_latitude']}, lon {g['requested_longitude']}, {manifest['request']['start_date']} to {manifest['request']['end_date']} (UTC days; period source: {manifest['request']['period_source']}).",
        f"- Retrieved {r['retrieved_at_utc']} ; {r['response_bytes']:,} bytes; SHA-256 `{r['response_sha256']}`.",
        f"- ERA5 cell returned by the API: lat {g['returned_latitude']}, lon {g['returned_longitude']}, elevation {g['elevation_m']} m "
        f"(about {g['approx_offset_km']:.1f} km from the requested point).",
        "- Dataset version: not reported by the API; the retrieval time above identifies the data state.", "",
        "## 2. Time coverage",
        f"- {v['first_utc']} to {v['last_utc']} (UTC); local {v['first_local']} to {v['last_local']}.",
        f"- {v['rows']:,} rows; expected {v['expected_hours']:,} hourly steps; missing hours {v['missing_hours']}; coverage {v['hourly_coverage_pct']}%.",
        f"- Duplicate timestamps in the response: {v['duplicate_timestamps']}; collapsed {c['exact_duplicates_collapsed']}, conflicting {c['conflicting_duplicate_timestamps']}.", "",
        "## 3. Variables", "| variable | unit | valid | missing | outside plausible range | min | mean | max |", "|---|---|---|---|---|---|---|---|",
    ]
    for name, s in v["variables"].items():
        f = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
        lines.append(f"| {name} | {s['unit']} | {s['n_valid']:,} | {s['n_missing']:,} | {s['n_outside_plausible_range']} | {f(s['min'])} | {f(s['mean'])} | {f(s['max'])} |")
    gaps = [(n, s) for n, s in v["variables"].items() if s["n_missing"]]
    if gaps:
        lines += ["", "**Missing values (kept missing, never filled):**"]
        lines += [f"- {n}: {s['n_missing']:,} hours in {s['missing_runs']} run(s), longest {s['longest_missing_run_hours']:,} h, between {s['first_missing_utc']} and {s['last_missing_utc']} (UTC)." for n, s in gaps]
    if manifest["variables_not_retrieved"]:
        lines += ["", f"**Variables NOT available in the response:** {', '.join(manifest['variables_not_retrieved'])}."]
    lines += ["", "## 4. Decisions", *[f"- {d}" for d in DECISIONS], "", "## 5. Limitations", *[f"- {x}" for x in manifest["known_limitations"]], "",
              "## 6. Licence and attribution", *[f"- {x}" for x in ATTRIBUTION.values()],
              "- Free-tier terms: non-commercial use only; fewer than 10,000 calls/day. This pipeline makes one request and caches it."]
    return "\n".join(lines) + "\n"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return float(12742 * np.arcsin(np.sqrt(a)))


def run(raw_dir: Path, interim_dir: Path, processed_dir: Path, manifest_path: Path, *, lat: float = wc.DEFAULT_LAT, lon: float = wc.DEFAULT_LON,
        start: str | None = None, end: str | None = None, refresh: bool = False, allow_overwrite: bool = False, http_get=wc.default_http_get,
        embassy_manifest: Path = EMBASSY_MANIFEST) -> dict:
    if start is None or end is None:
        s0, e0, period_src = embassy_period(embassy_manifest)
        start, end = start or s0, end or e0
    else:
        period_src = "command line"
    req = wc.Request(lat, lon, start, end)
    fetched = wc.fetch(req, raw_dir, http_get=http_get, refresh=refresh)
    doc = json.loads(fetched.raw_path.read_bytes())
    df, info = wc.parse(doc)
    validation = wc.validate(df)
    clean, dstats = dedupe(df, tuple(wc.VARIABLES))
    if validation["duplicate_timestamps"] == 0:
        dstats = {"exact_duplicates_collapsed": 0, "conflicting_duplicate_timestamps": 0}

    outputs = {}
    interim_p = interim_dir / "era5_hourly_parsed.parquet"
    processed_p = processed_dir / "era5_hourly.parquet"
    report_p = interim_dir / "weather_validation_report.md"
    outputs["interim_parsed"] = (interim_p, io.write_parquet(df, interim_p, {"stage": "interim", "version": PROCESSING_VERSION}, allow_overwrite))
    meta = {"version": PROCESSING_VERSION, "source": "Open-Meteo Historical Weather API, models=era5", "units": info["units"], "timezone_local": wc.LOCAL_TZ}
    outputs["processed_hourly"] = (processed_p, io.write_parquet(clean, processed_p, meta, allow_overwrite))

    approx = haversine_km(lat, lon, info["returned_latitude"], info["returned_longitude"]) if info["returned_latitude"] is not None else float("nan")
    manifest = {
        "schema_version": 1, "stage": "phase2_weather", "generated_at_utc": mf.utc_now(),
        "processing": {"version": PROCESSING_VERSION, "script": SCRIPT, "python": platform.python_version(), "pandas": pd.__version__, "pyarrow": pyarrow.__version__, "platform": sys.platform},
        "source": {"provider": "Open-Meteo", "endpoint": wc.ENDPOINT, "model": wc.MODEL, "data_kind": "reanalysis (modelled historical weather; not a forecast, not a local measurement)",
                   "spatial_resolution": "about 0.25 degrees (~25 km)", "dataset_version": "not reported by the API"},
        "request": {"url": req.url(), "params": req.params(), "start_date": start, "end_date": end, "period_source": period_src},
        "retrieval": {"retrieved_at_utc": fetched.retrieved_at_utc, "downloaded_in_this_run": fetched.downloaded, "http_status": fetched.http_status, "response_bytes": fetched.n_bytes,
                      "response_sha256": fetched.sha256, "raw_file": io.rel(fetched.raw_path), "request_record": io.rel(fetched.request_path)},
        "grid": {"requested_latitude": lat, "requested_longitude": lon, "returned_latitude": info["returned_latitude"], "returned_longitude": info["returned_longitude"],
                 "elevation_m": info["elevation_m"], "approx_offset_km": approx},
        "time": {"utc_offset_seconds": info["utc_offset_seconds"], "response_timezone": info["timezone"], "local_timezone": wc.LOCAL_TZ,
                 "note": "Timestamps are UTC instants; precipitation is the sum of the preceding hour, other variables are instantaneous."},
        "variables": {v: {"unit": info["units"].get(v), "label": wc.VARIABLES[v]["label"]} for v in wc.VARIABLES if v in info["units"]},
        "variables_not_retrieved": info["variables_missing_from_response"],
        "validation": validation, "coverage": dstats,
        "outputs": {k: {"path": io.rel(p), "status": st, "sha256": mf.hash_file(p, ("sha256",))["sha256"], "bytes": p.stat().st_size} for k, (p, st) in outputs.items()},
        "decisions": DECISIONS, "known_limitations": LIMITATIONS, "licence": {"data": "CC BY 4.0 (Open-Meteo)", "attribution": ATTRIBUTION,
                                                                           "usage": "Free API: non-commercial use only; commercial use requires a paid API key."},
    }
    for n, sv in validation["variables"].items():
        if sv["n_missing"]:
            manifest["known_limitations"] = [*manifest["known_limitations"],
                                             f"{n} has {sv['n_missing']:,} missing hours ({sv['first_missing_utc']} to {sv['last_missing_utc']}, UTC) in the ERA5 response; they are not filled."]
    text = report_text(manifest)
    outputs["report"] = (report_p, io.guarded_write(report_p, lambda p: p.write_text(text, encoding="utf-8"), allow_overwrite))
    manifest["outputs"]["report"] = {"path": io.rel(report_p), "status": outputs["report"][1], "sha256": mf.hash_file(report_p, ("sha256",))["sha256"], "bytes": report_p.stat().st_size}
    mf.write_manifest_atomic(Path(manifest_path), manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=wc.DEFAULT_LAT)
    ap.add_argument("--lon", type=float, default=wc.DEFAULT_LON)
    ap.add_argument("--start", help="YYYY-MM-DD (default: Embassy first day)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: Embassy last day)")
    ap.add_argument("--refresh", action="store_true", help="download again even if a cached response exists")
    ap.add_argument("--allow-overwrite", action="store_true")
    a = ap.parse_args(argv)
    try:
        m = run(REPO_ROOT / "data/raw/weather", REPO_ROOT / "data/interim/weather", REPO_ROOT / "data/processed/weather", PROCESSING_MANIFEST,
                lat=a.lat, lon=a.lon, start=a.start, end=a.end, refresh=a.refresh, allow_overwrite=a.allow_overwrite)
    except (wc.WeatherError, io.OutputExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    v = m["validation"]
    print(f"{'Downloaded' if m['retrieval']['downloaded_in_this_run'] else 'Used cached response'}: {v['rows']} rows, {v['first_utc']} .. {v['last_utc']}, coverage {v['hourly_coverage_pct']}%")
    print(f"Grid cell {m['grid']['returned_latitude']}, {m['grid']['returned_longitude']} ({m['grid']['approx_offset_km']:.1f} km from the requested point)")
    for k, o in m["outputs"].items():
        print(f"  {k}: {o['path']} [{o['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
