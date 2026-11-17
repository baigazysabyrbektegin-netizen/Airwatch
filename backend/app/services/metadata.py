"""Dataset, provenance, vintage, limitation and licence information, assembled from the processing manifests."""

from __future__ import annotations

from app.services.data_store import MANIFESTS, DataStore

# Statements the interface must always make. They restate docs/DECISIONS.md and the stage manifests.
PRESENTATION_RULES = [
    "US Embassy readings are measured historical data (2019-02-06 to 2024-06-30).",
    "Clarity readings are low-cost sensor measurements from 2021; their calibration is reported by the source paper but not independently verified.",
    "CAMx is historical modelled PM2.5, not current measured pollution.",
    "Emissions are a 2018 bottom-up inventory (modelled, not measured). CAMx and the inventory are related, not independent evidence of each other.",
    "Population is a 2019 modelled grid estimate.",
    "Roads and context layers have their own, often unstated, source vintages. Road density is not traffic volume.",
    "The 1-hour offset between the Clarity and Embassy time labels is unresolved; the series are never aligned or merged.",
    "Embassy readings from about April 2023 to June 2024 show a potential measurement-regime issue.",
    "The Embassy coordinates are approximate, not verified monitor coordinates.",
    "No current citywide sensor coverage dataset is available; all layers describe past conditions or models.",
]
BASEMAP = {
    "default_provider": "OpenFreeMap",
    "usage": "No API key or registration; the site states no limits on map views and that commercial use is allowed; no SLA is offered (availability can change without notice).",
    "attribution": "OpenFreeMap © OpenMapTiles, data from OpenStreetMap",
    "policy_url": "https://openfreemap.org/tos/",
    "fallback": "If the basemap cannot be loaded the map shows the project's own data layers on a plain background.",
}


OPTIONAL_MANIFESTS = {"weather"}


def _unit_and_kind(gis: dict | None) -> dict:
    kinds = (gis or {}).get("data_kinds", {})
    vint = (gis or {}).get("data_vintage", {})
    res = (gis or {}).get("spatial_resolution", {})
    return kinds, vint, res


def build(store: DataStore) -> dict:
    mans = {k: store.manifest_or_none(k) for k in MANIFESTS}
    zen, emb, cla, gis = (mans[k] for k in ("zenodo", "embassy", "clarity", "gis"))
    wea = mans.get("weather")  # optional: created by `python -m pipeline.process_weather`; its absence is not an error for the map
    kinds, vint, res = _unit_and_kind(gis)
    record = (zen or {}).get("record", {})
    gis_lim = (gis or {}).get("known_limitations", [])
    emb_review = bool(((emb or {}).get("source_archive") or {}).get("provenance_review_required", True))
    cla_review = bool(((cla or {}).get("licence") or {}).get("provenance_review_required", True))

    datasets = []
    if emb:
        d = emb.get("dataset", {})
        datasets.append({"id": "embassy_pm25", "title": "US Embassy Bishkek hourly PM2.5", "data_kind": "measured", "unit": "µg/m³",
                         "vintage": f"{(d.get('first_timestamp_local') or '')[:10]} to {(d.get('last_timestamp_local') or '')[:10]} (historical)", "resolution": "hourly, one site (approximate location)",
                         "processing_version": emb.get("processing", {}).get("version"), "provenance_review_required": emb_review, "limitations": emb.get("known_limitations", []),
                         "quality_summary": d.get("quality_flag_counts_hourly")})
    if cla:
        d = cla.get("dataset_summary", {})
        datasets.append({"id": "clarity_pm25", "title": "Clarity low-cost sensors, 2021", "data_kind": "measured (low-cost sensors)", "unit": "µg/m³",
                         "vintage": f"{(d.get('first_interval_start_local') or '')[:10]} to {(d.get('last_interval_start_local') or '')[:10]} (historical)",
                         "resolution": f"hourly, {d.get('devices_with_data')} sensors with data ({d.get('devices_listed')} listed)", "processing_version": cla.get("processing", {}).get("version"),
                         "provenance_review_required": cla_review, "limitations": cla.get("known_limitations", []), "timestamp_assumptions": cla.get("timestamp_assumptions")})
    if gis:
        reso = f"{res.get('grid_cells')} cells of {res.get('cell_size_deg')}° (~0.9 km²)"
        for did, title, key, unit in (("camx_pm25", "CAMx modelled PM2.5", "camx", "µg/m³"), ("emissions_2018", "Gridded emissions inventory", "emissions", "t/cell/yr"),
                                      ("population", "Population grid", "population", "persons per cell"), ("roads", "Roads", "roads", "km, km/km²"),
                                      ("context_layers", "Mapped context layers", "context_layers", None)):
            datasets.append({"id": did, "title": title, "data_kind": kinds.get(key), "unit": unit, "vintage": vint.get(key), "resolution": reso if key != "roads" else "vector, clipped to the grid",
                             "processing_version": gis.get("processing", {}).get("version"), "provenance_review_required": True, "limitations": gis_lim})
    if wea:
        v = wea.get("validation", {})
        datasets.append({"id": "era5_weather", "title": "ERA5 reanalysis weather (via Open-Meteo)", "data_kind": "reanalysis (modelled historical weather; not a forecast, not a local measurement)",
                         "unit": "°C, %, m/s, °, hPa, mm, m", "vintage": f"{(v.get('first_local') or '')[:10]} to {(v.get('last_local') or '')[:10]} (historical)",
                         "resolution": "hourly, ~0.25° grid cell (~25 km)", "processing_version": wea.get("processing", {}).get("version"), "provenance_review_required": False,
                         "limitations": wea.get("known_limitations", [])})
    return {
        "available_manifests": [k for k, v in mans.items() if v], "unavailable_manifests": [store.rel(k) for k, v in mans.items() if not v and k not in OPTIONAL_MANIFESTS],
        "source_record": {"id": record.get("record_id"), "doi": record.get("doi"), "title": record.get("title"), "publication_date": record.get("publication_date"),
                          "licence": record.get("licence"), "creators": record.get("creators"), "url": record.get("record_url")},
        "datasets": datasets,
        "timestamp_conventions": {"clarity": (cla or {}).get("timestamp_assumptions"), "embassy": "Date (LT) as given by the source, Asia/Bishkek; start/end of hour not documented",
                                  "alignment": "1-hour offset between Clarity and Embassy labels is unresolved; series are never merged"},
        "coordinate_reference": {"output_crs": (gis or {}).get("output_crs", "EPSG:4326"), "note": "Source CRS is undeclared for the grid, population and road shapefiles; WGS84 is an assumption (see gis_processing.json)."},
        "presentation_rules": PRESENTATION_RULES,
        "limitations": {"embassy": (emb or {}).get("known_limitations", []), "clarity": (cla or {}).get("known_limitations", []), "gis": gis_lim,
                        **({"weather": wea.get("known_limitations", [])} if wea else {})},
        "licence": {"record_level": record.get("licence"), "attribution_required": True,
                    "attribution": ((cla or {}).get("licence") or {}).get("attribution") or "Guttikunda, S. (2024). Zenodo. doi:10.5281/zenodo.12720883 (CC BY 4.0).",
                    "notes": ((gis or {}).get("licence") or {}).get("notes", []),
                    "other": "Roads are OpenStreetMap-derived (ODbL, share-alike); the population product's terms are unverified. Review before redistribution.",
                    **({"weather": ((wea.get("licence") or {}).get("attribution") or {}), "weather_usage": (wea.get("licence") or {}).get("usage")} if wea else {})},
        "basemap": BASEMAP,
    }
