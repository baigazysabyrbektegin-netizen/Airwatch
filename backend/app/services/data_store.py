"""Read-only, cached access to the PROCESSED datasets and the processing manifests.

Only ``data/processed/`` and ``data/manifest/`` are read; raw and interim data are never touched, and nothing is
written. Each file is read once per process and kept in memory. A missing file raises ``DataUnavailable`` so the API
can answer with an informative error instead of a stack trace (and never substitutes zeros or empty data).
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import geopandas as gpd
import pandas as pd

from app.services.errors import DataUnavailable

LOCAL_TZ = "Asia/Bishkek"

FILES = {
    "clarity_inventory": "processed/clarity/clarity_device_inventory.csv",
    "clarity_hourly": "processed/clarity/clarity_pm25_hourly.parquet",
    "clarity_daily": "processed/clarity/clarity_daily.parquet",
    "clarity_monthly": "processed/clarity/clarity_monthly.parquet",
    "embassy_hourly": "processed/embassy/embassy_pm25_hourly.parquet",
    "grid": "processed/gis/airwatch_spatial_grid.parquet",
    "roads": "processed/gis/roads.parquet",
    "emission_points": "processed/gis/emissions_point_sources.parquet",
    "context": "processed/gis/context_layers.geojson",
    "admin": "processed/gis/administrative_boundaries.geojson",
    "weather_hourly": "processed/weather/era5_hourly.parquet",
}
MANIFESTS = {
    "zenodo": "manifest/zenodo_12720883.json",
    "embassy": "manifest/embassy_processing.json",
    "clarity": "manifest/clarity_processing.json",
    "gis": "manifest/gis_processing.json",
    "weather": "manifest/weather_processing.json",
}
HINTS = {
    "weather_hourly": "Create it once with: python -m pipeline.process_weather (downloads ERA5 from Open-Meteo, then caches it).",
    "weather": "Create it once with: python -m pipeline.process_weather (downloads ERA5 from Open-Meteo, then caches it).",
}


class DataStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._cache: dict[str, Any] = {}
        self._lock = RLock()

    # ------------------------------------------------------------------ plumbing
    def path(self, key: str) -> Path:
        return self.data_dir / (FILES.get(key) or MANIFESTS[key])

    def rel(self, key: str) -> str:
        return "data/" + (FILES.get(key) or MANIFESTS[key])

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def cached(self, name: str, build: Callable[[], Any]) -> Any:
        with self._lock:
            if name not in self._cache:
                self._cache[name] = build()
            return self._cache[name]

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _load(self, key: str, reader: Callable[[Path], Any]) -> Any:
        def build():
            p = self.path(key)
            if not p.is_file():
                raise DataUnavailable(self.rel(key), hint=HINTS.get(key))
            try:
                return reader(p)
            except Exception as exc:  # unreadable/corrupt file: report it, do not crash
                raise DataUnavailable(self.rel(key), f"could not be read ({type(exc).__name__})", HINTS.get(key)) from exc

        return self.cached(f"file:{key}", build)

    # ------------------------------------------------------------------ tables
    def clarity_inventory(self) -> pd.DataFrame:
        return self._load("clarity_inventory", pd.read_csv)

    def clarity_hourly(self) -> pd.DataFrame:
        return self._load("clarity_hourly", pd.read_parquet)

    def clarity_daily(self) -> pd.DataFrame:
        return self._load("clarity_daily", pd.read_parquet)

    def clarity_monthly(self) -> pd.DataFrame:
        return self._load("clarity_monthly", pd.read_parquet)

    def embassy_hourly(self) -> pd.DataFrame:
        return self._load("embassy_hourly", pd.read_parquet)

    def weather_hourly(self) -> pd.DataFrame:
        return self._load("weather_hourly", pd.read_parquet)

    # ------------------------------------------------------------------ geodata
    def grid(self) -> gpd.GeoDataFrame:
        return self._load("grid", gpd.read_parquet)

    def roads(self) -> gpd.GeoDataFrame:
        return self._load("roads", gpd.read_parquet)

    def emission_points(self) -> gpd.GeoDataFrame:
        return self._load("emission_points", gpd.read_parquet)

    def context(self) -> gpd.GeoDataFrame:
        return self._load("context", gpd.read_file)

    def admin(self) -> gpd.GeoDataFrame:
        return self._load("admin", gpd.read_file)

    # ------------------------------------------------------------------ manifests
    def manifest(self, key: str) -> dict:
        def reader(p: Path) -> dict:
            return json.loads(p.read_text(encoding="utf-8"))

        return self._load(key, reader)

    def manifest_or_none(self, key: str) -> dict | None:
        try:
            return self.manifest(key)
        except DataUnavailable:
            return None

    def availability(self) -> dict[str, bool]:
        return {k: self.exists(k) for k in list(FILES) + list(MANIFESTS)}
