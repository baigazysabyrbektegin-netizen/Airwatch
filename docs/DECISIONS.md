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

## Open items
- Map tile provider: usage policy must be verified before one is chosen. Not selected yet.
