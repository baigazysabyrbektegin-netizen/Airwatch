# Project decisions

Recorded decisions that shape the data model and the wording of the user interface.
Each is provisional unless stated otherwise and can be changed by the project owner.

## Sensor-planner defaults (provisional)
- Score: `D × (w_E · E + w_S · S)` where D is coverage deficit, E is exposure burden
  (population × modelled winter PM2.5, percentile-ranked) and S is source context
  (smoothed 2018 emission density, percentile-ranked).
- Default weights: `w_E = 0.7`, `w_S = 0.3`. These are a transparent starting judgement, not estimates;
  no data can calibrate them.
- Presets: exposure-first (0.85 / 0.15), balanced (0.70 / 0.30), source-first (0.30 / 0.70).
- Representativeness radius `λ = 3 km` (provisional, from the winter spatial variability of the
  2021 Clarity sensors). Configurable between 1.5 and 5 km.

## Scope
- OpenAQ and live monitoring are **outside the initial MVP**.
- Optional current-station data enters through **CSV upload**. Historical coverage (2021) and
  present-day coverage are always shown as separate, dated layers.

## Wording rules for the UI and documentation
- The US Embassy coordinates are **approximate** (OSM embassy building point), not exact monitor coordinates.
- Clarity calibration is **not independently verified**. Keep two statements apart:
  1. Reported: the source paper says the sensors had "periodic calibrations".
  2. Inspected: we have seen no calibration method or parameters. Only a single co-located comparison
     with the embassy monitor (one sensor) was examined.
- CAMx (2018) and the 2018 emissions inventory are **related** modelled evidence, not independent:
  the inventory is the input to CAMx. Never present agreement between them as confirmation.
- Every value carries a `data_kind`: measured, modelled, reanalysis, satellite or static.
  Mock or placeholder values must never be shown as real measurements.

## Map tile provider (decided in Stage 5, Phase 1)
- **Chosen: OpenFreeMap** (`https://tiles.openfreemap.org/styles/positron`, vector tiles built from OpenStreetMap).
  Policy checked at <https://openfreemap.org/tos/>: no API key, no registration, no stated request limits,
  commercial use allowed, attribution to OpenFreeMap, OpenMapTiles and OpenStreetMap required, and **no SLA**
  (the service may change or stop without notice). Nothing in the frontend source is secret.
- Because there is no SLA the app never depends on it: if the style cannot be fetched within 6 s the data layers are
  drawn on a plain background and the layer panel says so. The basemap can also be switched off by the user.
- Rejected: the public OpenStreetMap tile server (its usage policy restricts heavy/app use and requires a
  fallback plan; not suitable as a default for a demo), Carto basemaps (free use is tied to Carto's own terms and
  attribution conditions that were not re-verified for a competition demo), MapTiler (needs an API key, which must not be put in the frontend).
- Caching: tiles are cached by the browser only; the project does not store or re-host tiles.
- Attribution shown on the map: OpenFreeMap, OpenMapTiles, OpenStreetMap, plus the study-data and ODbL road credits.

## Weather and the Data & Weather Explorer (Phase 2)
- Weather source: ERA5 reanalysis through the Open-Meteo Historical Weather API with `models=era5` (explicit). Reanalysis, not a forecast, not a local station.
  The nearest ERA5 cell returned for (42.917, 74.510) is centred at (43.0, 74.5), about 9 km away and ~25 km wide.
- Licence: CC BY 4.0 with attribution "Weather data by Open-Meteo.com"; the free API is non-commercial only. A commercial deployment needs a paid key.
- PM2.5 statistics use VALID hours only; excluded, suspect, flagged-but-included and imputed hours are counted separately. Days need 18 valid hours, months 75% of days.
- Weather/PM2.5 pairing uses a stated reference time and a user-chosen offset (default 0, hourly only). Nothing is tuned to improve correlation; the Embassy/Clarity 1-hour offset stays unresolved and the networks are never merged.
- Hourly explorer requests are limited to 120 days; longer ranges use daily/monthly aggregation done in the backend.
- Correlations (Pearson and Spearman) are shown only with at least 30 valid pairs and are labelled as association, never causation.

## Open items
- None for the map provider. Re-verify the OpenFreeMap policy before any public deployment.
