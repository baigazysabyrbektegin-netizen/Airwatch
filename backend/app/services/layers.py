"""The layer catalogue and the GeoJSON behind each map layer. Every layer states its data kind, vintage and limits."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from app.services import geo, grid as grid_svc
from app.services import stations as st
from app.services.data_store import DataStore
from app.services.errors import BadQuery, DataUnavailable, NotFound

ROAD_GROUPS = ["primary", "secondary", "tertiary", "local_streets", "unclassified", "non_motorised_ways", "other_unlisted"]
ROAD_DEFAULT = ["primary", "secondary", "tertiary"]

LAYERS = [
    {"id": "stations", "title": "Monitoring stations", "group": "measurements", "geometry_type": "Point", "data_kind": "measured",
     "vintage": "Clarity 2021-02-28 to 2021-12-31; US Embassy 2019-02-06 to 2024-06-30 (historical)", "unit": None, "value_field": None, "needs": ["clarity_inventory", "clarity_hourly", "embassy_hourly"],
     "source": "Zenodo record 12720883 (Clarity sensor workbook; US Embassy files)", "endpoint": "/api/layers/stations", "query_defaults": {},
     "description": "Clarity low-cost sensors with readings in 2021 and the US Embassy monitor. Historical measurements, not a live network.",
     "limitations": ["Clarity calibration is reported by the source paper but not independently verified.", "The US Embassy position is APPROXIMATE (OpenStreetMap building point).",
                     "No current citywide sensor coverage dataset is available: this is 2021 coverage plus a 2019-2024 single monitor.",
                     "Clarity and Embassy time labels have an unresolved 1-hour offset; they are not aligned."]},
    {"id": "population_grid", "title": "Population (2019 modelled grid)", "group": "estimates", "geometry_type": "Polygon", "data_kind": "estimated (gridded population estimate, not a census count)",
     "vintage": "2019 (annual 2000-2019 available)", "unit": "persons per cell", "value_field": "pop_2019", "needs": ["grid"], "source": "Zenodo bishkek_pop_allyears.csv (deck names LandScan; unverified)",
     "endpoint": "/api/layers/population_grid", "query_defaults": {},
     "description": "Gridded population estimate on the 0.01° (~0.9 km²) grid.", "limitations": ["Unit is assumed (persons per cell); none is stated in the source.", "Provenance and licence of the population product are unverified."]},
    {"id": "camx_pm25", "title": "CAMx modelled PM2.5", "group": "modelled", "geometry_type": "Polygon", "data_kind": "MODELLED (historical; not measured)",
     "vintage": "year not stated in the file (accompanying figures named 2018)", "unit": "µg/m³", "value_field": "camx_pm25_ugm3", "needs": ["grid"], "source": "Zenodo bishkek_camx_pm25_monthlyavg.csv",
     "endpoint": "/api/layers/camx_pm25", "query_defaults": {"camx_month": "annual"},
     "description": "Monthly and annual mean PM2.5 from the WRF-CAMx model. Not current pollution and not a measurement.",
     "limitations": ["Model output driven by the 2018 emissions inventory; not independent evidence of that inventory.", "Monthly averages only; no daily or hourly resolution.", "The model year is not stated in the file."]},
    {"id": "emissions_context", "title": "PM2.5 emissions (2018 inventory)", "group": "modelled", "geometry_type": "Polygon", "data_kind": "MODELLED bottom-up inventory (not measured)",
     "vintage": "2018 base year", "unit": "t/km²/yr (also t/cell/yr)", "value_field": "emis_pm25_total_t_per_km2_yr", "needs": ["grid"], "source": "Zenodo bishkek_gridded_emissions_2018.rar",
     "endpoint": "/api/layers/emissions_context", "query_defaults": {},
     "description": "PM2.5 = FRPM + PEC + POA per cell. Point sources (brick kilns, heavy industry) are already included in the cell totals.",
     "limitations": ["2018 snapshot; not current emissions.", "Sector names are inferred from file names.", "The related CAMx run used this inventory as input."]},
    {"id": "roads", "title": "Roads (OSM-derived)", "group": "static", "geometry_type": "LineString", "data_kind": "static geographic (OpenStreetMap-derived)", "vintage": "OSM extract date not stated",
     "unit": None, "value_field": None, "needs": ["roads"], "source": "Zenodo roads shapefile (OpenStreetMap)", "endpoint": "/api/layers/roads", "query_defaults": {"groups": ",".join(ROAD_DEFAULT)},
     "description": "Road network by OSM class. Shown here: primary (incl. trunk), secondary and tertiary by default.",
     "limitations": ["Road length and density are not traffic volume.", "Road names were lost in the source files and are not available.", "OSM data is under the ODbL licence (attribution, share-alike)."]},
    {"id": "context_features", "title": "Emission-source context (mapped)", "group": "static", "geometry_type": "Mixed", "data_kind": "static geographic (hand-mapped features)", "vintage": "not stated",
     "unit": None, "value_field": None, "needs": ["context"], "source": "Zenodo KML layers", "endpoint": "/api/layers/context_features", "query_defaults": {},
     "description": "Boilers, brick kilns, industrial areas, landfill, quarries, water bodies and main roads.",
     "limitations": ["Almost no attributes: only the boilers are named.", "The meaning of two polygon layers (dh_districts, main_province) is unverified.", "Source dates are not stated."]},
    {"id": "administrative_boundary", "title": "Administrative boundary (country level)", "group": "static", "geometry_type": "Polygon", "data_kind": "static geographic", "vintage": "not stated",
     "unit": None, "value_field": None, "needs": ["admin"], "source": "Zenodo bishkek_adm2.shp", "endpoint": "/api/layers/administrative_boundary", "query_defaults": {},
     "description": "Kyrgyzstan country polygon clipped to the study area.", "limitations": ["NOT Bishkek's city or district boundary: none exists in the data."]},
    {"id": "spatial_grid", "title": "Integrated spatial grid", "group": "static", "geometry_type": "Polygon", "data_kind": "mixed (each field keeps its own kind and vintage)", "vintage": "see each field",
     "unit": None, "value_field": None, "needs": ["grid"], "source": "AirWatch integrated grid (Stage 4)", "endpoint": "/api/grid", "query_defaults": {},
     "description": "2,000 cells of 0.01°. Use /api/grid to select fields and filters.", "limitations": ["Integration does not make any source more accurate."]},
]


def _count(store: DataStore, layer_id: str) -> int | None:
    try:
        if layer_id == "stations":
            return len(st.list_stations(store))
        if layer_id in ("population_grid", "camx_pm25", "emissions_context", "spatial_grid"):
            return int(len(store.grid()))
        if layer_id == "roads":
            return int(len(store.roads()))
        if layer_id == "context_features":
            return int(len(store.context()))
        if layer_id == "administrative_boundary":
            return int(len(store.admin()))
    except DataUnavailable:
        return None
    return None


def catalogue(store: DataStore) -> list[dict]:
    out = []
    for L in LAYERS:
        missing = [store.rel(k) for k in L["needs"] if not store.exists(k)]
        item = {k: v for k, v in L.items() if k != "needs"}
        item.update(available=not missing, unavailable_reason=("missing: " + ", ".join(missing)) if missing else None,
                    feature_count=None if missing else _count(store, L["id"]), default_visible=L["id"] == "stations", time_status="historical")
        out.append(item)
    return out


def _station_layer(store: DataStore, include_no_data: bool) -> dict:
    rows = [s for s in st.list_stations(store, include_no_data=include_no_data) if s["latitude"] is not None]
    feats = []
    for s in rows:
        q = s["quality"] or {}
        feats.append({"type": "Feature", "id": s["id"], "geometry": {"type": "Point", "coordinates": [s["longitude"], s["latitude"]]}, "properties": {
            "id": s["id"], "name": s["name"], "network": s["network"], "sensor_type": s["sensor_type"], "grade": s["grade"], "data_kind": s["data_kind"],
            "location_status": s["location_status"], "data_available": s["data_available"], "n_valid_readings": s["n_valid_readings"], "coverage_pct": s["coverage_pct"],
            "period_start": (s["data_period"] or {}).get("start"), "period_end": (s["data_period"] or {}).get("end"), "vintage": s["vintage"],
            "shares_coordinates": bool(s["shares_coordinates_with"]), "approximate_location": s["location_status"] == "APPROXIMATE", "quality_notes": s["notes"],
            "valid_readings": q.get("valid_readings", q.get("valid_hours"))}})
    return {"type": "FeatureCollection", "features": feats, "metadata": {"count": len(feats), "notes": [st.ALIGNMENT_NOTE, "Historical stations only; no current coverage data exists."]}}


def layer_data(store: DataStore, layer_id: str, *, groups: str | None = None, layers: str | None = None, bbox: str | None = None, camx_month: str = "annual",
               include_no_data: bool = False) -> dict:
    ids = [L["id"] for L in LAYERS]
    if layer_id not in ids:
        raise NotFound(f"Unknown layer {layer_id!r}.", ids)
    try:
        box = geo.parse_bbox(bbox)
    except ValueError as exc:
        raise BadQuery(str(exc)) from exc
    if layer_id == "stations":
        return _station_layer(store, include_no_data)
    if layer_id in ("population_grid", "camx_pm25", "emissions_context", "spatial_grid"):
        presets = {"population_grid": "cell_id,pop_2019", "camx_pm25": "cell_id", "spatial_grid": None,
                   "emissions_context": "cell_id,emis_pm25_total_t_per_km2_yr,emis_pm25_total_t_yr,n_point_sources"}
        return grid_svc.query(store, camx_month=camx_month if layer_id == "camx_pm25" else "annual", fields=presets[layer_id] if layer_id != "camx_pm25" else "cell_id,camx_pm25_ugm3",
                              min_population=None, max_population=None, min_emissions=None, min_road_density=None, min_camx_pm25=None, max_camx_pm25=None,
                              bbox=bbox, include_geometry=True, limit=None, offset=0)
    if layer_id == "roads":
        want = ROAD_DEFAULT if groups in (None, "") else (ROAD_GROUPS if groups == "all" else [g.strip() for g in groups.split(",") if g.strip()])
        bad = [g for g in want if g not in ROAD_GROUPS]
        if bad:
            raise BadQuery(f"Unknown road group(s) {bad}.", ROAD_GROUPS + ["all"])
        r = store.roads()
        r = r[r["road_group"].isin(want) & r.geometry.notna()]
        if box is not None:
            r = r[geo.in_bbox(r, box)]
        return geo.feature_collection(r, ["osm_id", "fclass", "road_group", "oneway", "bridge", "tunnel"], precision=5, extra={"metadata": {
            "count": int(len(r)), "groups": want, "data_kind": "static geographic (OpenStreetMap-derived)", "vintage": "OSM extract date not stated",
            "notes": ["Road length and density are not traffic volume.", "Road names are not available (lost in the source files).", "OpenStreetMap contributors, ODbL."]}})
    if layer_id == "context_features":
        c = store.context()
        keys = sorted(c["layer"].unique())
        if layers:
            want = [x.strip() for x in layers.split(",") if x.strip()]
            bad = [x for x in want if x not in keys]
            if bad:
                raise BadQuery(f"Unknown context layer(s) {bad}.", keys)
            c = c[c["layer"].isin(want)]
        c = c[c.geometry.notna()]
        if box is not None:
            c = c[geo.in_bbox(c, box)]
        c = c.assign(feature_name=c["feature_name"].where(c["feature_name"].notna(), None))
        return geo.feature_collection(c, ["layer", "layer_label", "layer_meaning_status", "feature_name", "name_is_placeholder"], extra={"metadata": {
            "count": int(len(c)), "available_layers": keys, "data_kind": "static geographic (hand-mapped)", "vintage": "not stated",
            "notes": ["Layers with layer_meaning_status 'unverified' are shown as mapped; their meaning has not been verified."]}})
    if layer_id == "administrative_boundary":
        a = store.admin()
        return geo.feature_collection(a, ["admin_code", "admin_name", "admin_level_inferred"], extra={"metadata": {
            "count": int(len(a)), "notes": ["Country polygon clipped to the study area. It is NOT Bishkek's city or district boundary."]}})
    raise NotFound(f"Layer {layer_id!r} has no data endpoint.", ids)  # pragma: no cover
