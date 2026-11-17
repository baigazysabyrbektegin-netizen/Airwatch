"""Explainable, grid-based monitoring coverage and candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from app.services import stations as station_service
from app.services.data_store import DataStore
from app.services.errors import BadQuery, DataUnavailable

DEFAULT_RADIUS_KM = 3.0
DEFAULT_MIN_SEPARATION_KM = 2.0
DEFAULT_EXPOSURE_WEIGHT = 0.7
DEFAULT_SOURCE_WEIGHT = 0.3
MAX_CANDIDATES = 50


@dataclass(frozen=True)
class PlannerParameters:
    n: int = 5
    coverage_radius_km: float = DEFAULT_RADIUS_KM
    min_separation_km: float = DEFAULT_MIN_SEPARATION_KM
    exposure_weight: float = DEFAULT_EXPOSURE_WEIGHT
    source_weight: float = DEFAULT_SOURCE_WEIGHT

    def validate(self) -> None:
        if not 1 <= self.n <= MAX_CANDIDATES:
            raise BadQuery(f"n must be between 1 and {MAX_CANDIDATES}.", [1, MAX_CANDIDATES])
        if self.coverage_radius_km <= 0 or self.coverage_radius_km > 100:
            raise BadQuery("coverage_radius_km must be greater than 0 and at most 100.", [0, 100])
        if self.min_separation_km < 0 or self.min_separation_km > 100:
            raise BadQuery("min_separation_km must be between 0 and 100.", [0, 100])
        if self.exposure_weight < 0 or self.source_weight < 0 or self.exposure_weight + self.source_weight <= 0:
            raise BadQuery("exposure_weight and source_weight must be non-negative and not both zero.")


def _minmax(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(np.nan, index=values.index)
    lo, hi = numeric.min(), numeric.max()
    if hi == lo:
        return pd.Series(0.0, index=values.index)
    return (numeric - lo) / (hi - lo)


def _context_score(grid: gpd.GeoDataFrame) -> pd.Series:
    candidates = [
        c for c in grid.columns
        if ("context" in c.lower() or "industrial" in c.lower() or "boiler" in c.lower() or "kiln" in c.lower() or "quarry" in c.lower())
        and pd.api.types.is_numeric_dtype(grid[c])
    ]
    if not candidates:
        return pd.Series(np.nan, index=grid.index)
    return grid[candidates].fillna(0).sum(axis=1)


def _analysis(store: DataStore) -> gpd.GeoDataFrame:
    grid = store.grid().copy()
    try:
        crs = grid.crs
    except AttributeError as exc:
        raise DataUnavailable(store.rel("grid"), "the integrated spatial grid has no active geometry column", "Rebuild the GIS processed data with the pipeline GIS step.") from exc
    if crs is None:
        raise DataUnavailable(store.rel("grid"), "the integrated spatial grid has no CRS", "Rebuild the GIS processed data with the pipeline GIS step.")
    if "frac_in_kgz_polygons" not in grid:
        raise DataUnavailable(store.rel("grid"), "the integrated spatial grid has no Kyrgyzstan-overlap field", "Rebuild the GIS processed data with the pipeline GIS step.")
    grid = grid[grid["frac_in_kgz_polygons"].fillna(0) > 0].copy()
    clarity = [
        s for s in station_service.list_stations(store, network="clarity")
        if s.get("latitude") is not None and s.get("longitude") is not None
    ]
    if not clarity:
        raise DataUnavailable(store.rel("clarity_inventory"), "no active Clarity stations with coordinates are available", "Rebuild the Clarity processed data or verify the station inventory.")
    stations = gpd.GeoDataFrame(
        clarity,
        geometry=gpd.points_from_xy([s["longitude"] for s in clarity], [s["latitude"] for s in clarity]),
        crs="EPSG:4326",
    ).to_crs("EPSG:32643")
    metric = grid.to_crs("EPSG:32643")
    centroids = metric.geometry.centroid
    distances = np.column_stack([centroids.distance(point).to_numpy() / 1000 for point in stations.geometry])
    nearest_idx = distances.argmin(axis=1)
    grid["nearest_station_distance_km"] = distances.min(axis=1)
    grid["nearest_station_id"] = [clarity[i]["id"] for i in nearest_idx]
    grid["population_component"] = _minmax(grid["pop_2019"])
    grid["pollution_component"] = _minmax(grid["camx_pm25_annual_mean"])
    grid["emissions_component"] = _minmax(grid["emis_pm25_total_t_per_km2_yr"])
    grid["road_component"] = _minmax(grid["road_density_km_per_km2_total"])
    grid["context_component_raw"] = _context_score(grid)
    grid["context_component"] = _minmax(grid["context_component_raw"])
    grid["geometry"] = grid.geometry
    return grid


def _score(grid: gpd.GeoDataFrame, params: PlannerParameters, distances: pd.Series | None = None) -> pd.DataFrame:
    out = grid.copy()
    distance = distances if distances is not None else out["nearest_station_distance_km"]
    out["deficit_component"] = (distance / params.coverage_radius_km).clip(0, 1)
    exposure = out[["population_component", "pollution_component"]].mean(axis=1, skipna=True)
    source = out[["emissions_component", "road_component", "context_component"]].mean(axis=1, skipna=True)
    out["exposure_component"] = exposure
    out["source_component"] = source
    ew = params.exposure_weight / (params.exposure_weight + params.source_weight)
    sw = params.source_weight / (params.exposure_weight + params.source_weight)
    out["priority_score"] = out["deficit_component"] * (ew * exposure + sw * source)
    return out


def _confidence(row: pd.Series) -> str:
    missing = sum(pd.isna(row.get(c)) for c in ("population_component", "pollution_component", "emissions_component", "road_component", "context_component"))
    return "limited" if missing >= 2 else "moderate" if missing else "contextual"


def _candidate(row: pd.Series, rank: int, params: PlannerParameters) -> dict[str, Any]:
    reasons = []
    if row["deficit_component"] >= 0.75:
        reasons.append("far from the existing Clarity network")
    if row["population_component"] >= 0.75:
        reasons.append("higher 2019 gridded population")
    if row["pollution_component"] >= 0.75:
        reasons.append("higher historical modelled CAMx PM2.5")
    if row["source_component"] >= 0.75:
        reasons.append("stronger modelled emissions or contextual signal")
    if not reasons:
        reasons.append("balances monitoring deficit with available spatial context")
    components = {
        "monitoring_deficit": round(float(row["deficit_component"]), 4),
        "population_exposure": round(float(row["population_component"]), 4) if pd.notna(row["population_component"]) else None,
        "historical_modelled_pollution": round(float(row["pollution_component"]), 4) if pd.notna(row["pollution_component"]) else None,
        "emissions_context": round(float(row["emissions_component"]), 4) if pd.notna(row["emissions_component"]) else None,
        "road_context": round(float(row["road_component"]), 4) if pd.notna(row["road_component"]) else None,
        "environmental_context": round(float(row["context_component"]), 4) if pd.notna(row["context_component"]) else None,
    }
    return {
        "candidate_id": f"cell-{int(row['cell_id'])}",
        "rank": rank,
        "latitude": float(row["lat_c"]),
        "longitude": float(row["lon_c"]),
        "priority_score": round(float(row["priority_score"]), 5),
        "score_components": components,
        "population_2019": float(row["pop_2019"]) if pd.notna(row["pop_2019"]) else None,
        "camx_pm25_annual_mean_ugm3": float(row["camx_pm25_annual_mean"]) if pd.notna(row["camx_pm25_annual_mean"]) else None,
        "emissions_pm25_t_per_km2_yr": float(row["emis_pm25_total_t_per_km2_yr"]) if pd.notna(row["emis_pm25_total_t_per_km2_yr"]) else None,
        "nearest_station_id": row["nearest_station_id"],
        "nearest_station_distance_km": round(float(row["nearest_station_distance_km"]), 3),
        "data_confidence": _confidence(row),
        "explanation": "; ".join(reasons) + ".",
        "limitations": [
            "Grid centroid is an approximate investigation location, not a confirmed installation site.",
            "CAMx and emissions are modelled historical products; population is a 2019 estimate.",
            "Distance and score weights are planning assumptions, not validated exposure probabilities.",
        ],
    }


def _select(grid: gpd.GeoDataFrame, params: PlannerParameters) -> list[dict[str, Any]]:
    selected: list[pd.Series] = []
    remaining = grid.copy()
    for _ in range(params.n):
        if remaining.empty:
            break
        if selected:
            metric = remaining.to_crs("EPSG:32643")
            chosen_metric = gpd.GeoSeries([r.geometry for r in selected], crs="EPSG:4326").to_crs("EPSG:32643")
            centroids = metric.geometry.centroid
            selected_distances = np.column_stack([centroids.distance(p).to_numpy() / 1000 for p in chosen_metric])
            distances = pd.Series(np.minimum(remaining["nearest_station_distance_km"].to_numpy(), selected_distances.min(axis=1)), index=remaining.index)
            scored = _score(remaining, params, distances)
        else:
            scored = _score(remaining, params)
        scored = scored.sort_values(["priority_score", "cell_id"], ascending=[False, True])
        chosen = scored.iloc[0]
        selected.append(chosen)
        candidate = _candidate(chosen, len(selected), params)
        if selected:
            metric_remaining = remaining.to_crs("EPSG:32643")
            point = gpd.GeoSeries([chosen.geometry], crs="EPSG:4326").to_crs("EPSG:32643").iloc[0].centroid
            too_close = metric_remaining.geometry.centroid.distance(point) / 1000 < params.min_separation_km
            remaining = remaining.loc[~too_close]
        candidate["_row"] = chosen
        if selected:
            candidate["_selection_score"] = float(chosen["priority_score"])
        if "_results" not in locals():
            _results: list[dict[str, Any]] = []
        _results.append(candidate)
    for rank, candidate in enumerate(_results if "_results" in locals() else [], 1):
        candidate["rank"] = rank
        candidate.pop("_row", None)
        candidate.pop("_selection_score", None)
    return _results if "_results" in locals() else []


def candidates(store: DataStore, params: PlannerParameters) -> dict[str, Any]:
    params.validate()
    grid = _analysis(store)
    selected = _select(grid, params)
    return {
        "parameters": params.__dict__,
        "count": len(selected),
        "candidates": selected,
        "data_kind": "decision-support estimate",
        "notes": [
            "Clarity coordinates are the primary monitoring network; the approximate Embassy location is not used as an equivalent verified station.",
            "The greedy selection updates the distance deficit after each candidate and is not guaranteed globally optimal.",
            "No verified Bishkek city boundary is available; cells with positive Kyrgyzstan polygon overlap define the eligible study extent.",
        ],
    }


def overview(store: DataStore, params: PlannerParameters) -> dict[str, Any]:
    params.validate()
    grid = _analysis(store)
    covered = grid["nearest_station_distance_km"] <= params.coverage_radius_km
    pop = pd.to_numeric(grid["pop_2019"], errors="coerce")
    total_pop = float(pop.sum(min_count=1))
    underserved_pop = float(pop[~covered].sum(min_count=1))
    return {
        "existing_clarity_stations": len([s for s in station_service.list_stations(store, network="clarity") if s.get("latitude") is not None]),
        "eligible_grid_cells": int(len(grid)),
        "population_2019_total": total_pop,
        "population_2019_underserved": underserved_pop,
        "population_underserved_pct": round(100 * underserved_pop / total_pop, 2) if total_pop else None,
        "covered_cell_pct": round(100 * float(covered.mean()), 2),
        "coverage_radius_km": params.coverage_radius_km,
        "data_kind": "decision-support estimate",
        "limitations": [
            "Population is a 2019 gridded estimate, not current population or direct exposure measurement.",
            "A grid cell outside the radius is underserved under this planning assumption, not completely unmonitored.",
            "The country-level polygons are not a verified Bishkek boundary.",
        ],
    }


def coverage(store: DataStore, params: PlannerParameters) -> dict[str, Any]:
    params.validate()
    grid = _analysis(store)
    scored = _score(grid, params)
    features = []
    for _, row in scored.iterrows():
        features.append({
            "type": "Feature",
            "id": int(row["cell_id"]),
            "geometry": row.geometry.__geo_interface__,
            "properties": {
                "cell_id": int(row["cell_id"]),
                "coverage_status": "covered" if row["nearest_station_distance_km"] <= params.coverage_radius_km else "underserved",
                "nearest_station_distance_km": round(float(row["nearest_station_distance_km"]), 3),
                "population_2019": float(row["pop_2019"]) if pd.notna(row["pop_2019"]) else None,
                "priority_score": round(float(row["priority_score"]), 5),
            },
        })
    return {"type": "FeatureCollection", "features": features, "metadata": {"coverage_radius_km": params.coverage_radius_km, "data_kind": "decision-support estimate"}}
