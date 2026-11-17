"""GeoJSON helpers. Missing values become JSON null (never 0) and coordinates are rounded to keep payloads small."""

from __future__ import annotations

import json
from typing import Iterable

import geopandas as gpd
import pandas as pd
import shapely

GEOJSON_MEDIA_TYPE = "application/geo+json"


def records(df: pd.DataFrame, columns: Iterable[str]) -> list[dict]:
    """Rows as dicts; NaN/NaT -> null, numbers stay numbers."""
    cols = list(columns)
    if not cols:
        return [{} for _ in range(len(df))]
    return json.loads(df[cols].to_json(orient="records", double_precision=10))


def feature_collection(gdf: gpd.GeoDataFrame, columns: Iterable[str], *, precision: int = 6, include_geometry: bool = True,
                       extra: dict | None = None, ids: Iterable | None = None) -> dict:
    props = records(gdf, columns)
    if include_geometry and len(gdf):
        geoms = shapely.set_precision(gdf.geometry.values, 10.0 ** -precision)
        geometries = [json.loads(shapely.to_geojson(g)) if g is not None and not shapely.is_empty(g) else None for g in geoms]
    else:
        geometries = [None] * len(gdf)
    id_list = list(ids) if ids is not None else [None] * len(gdf)
    features = []
    for gm, pr, fid in zip(geometries, props, id_list):
        f = {"type": "Feature", "geometry": gm, "properties": pr}
        if fid is not None:
            f["id"] = fid
        features.append(f)
    out = {"type": "FeatureCollection", "features": features}
    if extra:
        out.update(extra)
    return out


def parse_bbox(text: str | None) -> tuple[float, float, float, float] | None:
    """'minlon,minlat,maxlon,maxlat' -> tuple, or None if not given. Raises ValueError with a clear message."""
    if text is None or text == "":
        return None
    try:
        parts = [float(x) for x in text.split(",")]
    except ValueError as exc:
        raise ValueError("bbox must be four comma-separated numbers: minlon,minlat,maxlon,maxlat") from exc
    if len(parts) != 4:
        raise ValueError("bbox must have exactly four values: minlon,minlat,maxlon,maxlat")
    minx, miny, maxx, maxy = parts
    if not (minx < maxx and miny < maxy):
        raise ValueError("bbox must satisfy minlon < maxlon and minlat < maxlat")
    if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise ValueError("bbox values must be valid longitudes (-180..180) and latitudes (-90..90)")
    return minx, miny, maxx, maxy


def in_bbox(gdf: gpd.GeoDataFrame, bbox: tuple[float, float, float, float]) -> pd.Series:
    """Features whose bounding box overlaps the requested box."""
    b = gdf.geometry.bounds
    return (b["maxx"] >= bbox[0]) & (b["minx"] <= bbox[2]) & (b["maxy"] >= bbox[1]) & (b["miny"] <= bbox[3])
