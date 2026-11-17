"""The integrated 0.01-degree spatial grid (2,000 cells), with filters, a selectable CAMx month and legend statistics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import geo
from app.services.data_store import DataStore
from app.services.errors import BadQuery

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
CAMX_CHOICES = ["annual"] + MONTHS
DEFAULT_FIELDS = ["cell_id", "ix", "iy", "pop_2019", "road_density_km_per_km2_total", "emis_pm25_total_t_yr", "emis_pm25_total_t_per_km2_yr",
                  "n_point_sources", "frac_in_kgz_polygons"]
POP, EMIS, ROAD = "pop_2019", "emis_pm25_total_t_yr", "road_density_km_per_km2_total"
CAMX_FIELD = "camx_pm25_ugm3"
NON_DATA = {"geometry"}

CAMX_INFO = {"definition": "MODELLED PM2.5 from the WRF-CAMx run (not a measurement); the selected month or the annual mean", "unit": "µg/m³",
             "data_kind": "MODELLED (historical; not current pollution)", "vintage": "year not stated in the file (accompanying figures named 2018)",
             "note": "Driven by the same 2018 emissions inventory as emis_* columns: not independent evidence of it."}


def allowed_fields(store: DataStore) -> list[str]:
    return [c for c in store.grid().columns if c not in NON_DATA] + [CAMX_FIELD]


def field_info(store: DataStore, fields: list[str], camx_month: str) -> dict:
    gis = store.manifest_or_none("gis") or {}
    cols = {e["column"]: e for e in gis.get("grid_columns", [])}
    out = {}
    for f in fields:
        if f == CAMX_FIELD:
            info = dict(CAMX_INFO)
            info["selection"] = camx_month
            out[f] = info
        elif f in cols:
            e = cols[f]
            out[f] = {k: e.get(k) for k in ("definition", "unit", "data_kind", "source", "vintage", "note") if e.get(k) not in (None, "")}
        else:
            out[f] = {"definition": "(see gis_processing.json)"}
    return out


def _q(series: pd.Series) -> dict:
    s = series.dropna()
    if s.empty or not pd.api.types.is_numeric_dtype(series):
        return {"n_null": int(series.isna().sum())}
    qs = np.quantile(s, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"min": float(s.min()), "p05": float(qs[0]), "p25": float(qs[1]), "median": float(qs[2]), "p75": float(qs[3]), "p95": float(qs[4]),
            "max": float(s.max()), "n_null": int(series.isna().sum())}


def query(store: DataStore, *, camx_month: str, fields: str | None, min_population: float | None, max_population: float | None, min_emissions: float | None,
          min_road_density: float | None, min_camx_pm25: float | None, max_camx_pm25: float | None, bbox: str | None, include_geometry: bool,
          limit: int | None, offset: int) -> dict:
    if camx_month not in CAMX_CHOICES:
        raise BadQuery(f"camx_month must be one of {CAMX_CHOICES}.", CAMX_CHOICES)
    grid = store.grid()
    allowed = allowed_fields(store)
    chosen = [f.strip() for f in fields.split(",") if f.strip()] if fields else list(DEFAULT_FIELDS)
    bad = [f for f in chosen if f not in allowed]
    if bad:
        raise BadQuery(f"Unknown field(s): {bad}.", allowed)
    if "cell_id" not in chosen:
        chosen.insert(0, "cell_id")
    try:
        box = geo.parse_bbox(bbox)
    except ValueError as exc:
        raise BadQuery(str(exc)) from exc
    camx_col = "camx_pm25_annual_mean" if camx_month == "annual" else f"camx_pm25_{camx_month}"
    if camx_col not in grid.columns:
        raise BadQuery(f"The grid file has no column {camx_col}; cannot provide CAMx {camx_month}.")
    g = grid.assign(**{CAMX_FIELD: grid[camx_col]})
    mask = pd.Series(True, index=g.index)
    for col, lo, hi in ((POP, min_population, max_population), (CAMX_FIELD, min_camx_pm25, max_camx_pm25)):
        if lo is not None:
            mask &= g[col] >= lo       # rows with a missing value never match a filter (they are excluded, not treated as 0)
        if hi is not None:
            mask &= g[col] <= hi
    if min_emissions is not None:
        mask &= g[EMIS] >= min_emissions
    if min_road_density is not None:
        mask &= g[ROAD] >= min_road_density
    if box is not None:
        mask &= geo.in_bbox(g, box)
    sel = g[mask].sort_values("cell_id")
    total = int(len(sel))
    page = sel.iloc[offset: offset + limit] if limit is not None else sel.iloc[offset:]
    fc = geo.feature_collection(page, chosen, include_geometry=include_geometry, ids=page["cell_id"].astype(int).tolist(), extra={
        "metadata": {
            "total": total, "count": int(len(page)), "offset": offset, "limit": limit, "grid_cells_in_dataset": int(len(grid)), "camx_month": camx_month,
            "fields": field_info(store, chosen, camx_month), "stats": {f: _q(sel[f]) for f in chosen if f not in ("cell_id", "ix", "iy")},
            "filters_applied": {k: v for k, v in {"min_population": min_population, "max_population": max_population, "min_emissions": min_emissions,
                                                  "min_road_density": min_road_density, "min_camx_pm25": min_camx_pm25, "max_camx_pm25": max_camx_pm25,
                                                  "bbox": bbox}.items() if v is not None},
            "notes": ["Each field keeps its own vintage and data kind (see 'fields'). Population is the 2019 modelled grid; CAMx is historical modelled PM2.5; "
                      "emissions are a 2018 bottom-up inventory (CAMx and emissions are related model products, not independent evidence).",
                      "Missing values are null, never 0; cells with a missing value are excluded by any filter on that field."],
        }})
    return fc
