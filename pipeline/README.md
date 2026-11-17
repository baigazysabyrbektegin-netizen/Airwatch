# Pipeline

Reproducible data-processing scripts. Each script writes only under `data/` and records provenance in
`data/manifest/`. It uses the standard library plus `httpx` (already in `backend/requirements.txt`).

## Stage 1: Zenodo ingestion (`ingest_zenodo.py`)

Downloads and verifies every file of Zenodo record
[12720883](https://zenodo.org/records/12720883) (Guttikunda, CC BY 4.0) and writes
`data/manifest/zenodo_12720883.json`. Raw files are stored **unchanged** in
`data/raw/zenodo_12720883/` (git-ignored). Archives are not extracted and nothing is parsed or cleaned.

Run from the repository root with the backend virtual environment:

```powershell
backend\.venv\Scripts\python.exe -m pipeline.ingest_zenodo --dry-run   # list files/sizes only
backend\.venv\Scripts\python.exe -m pipeline.ingest_zenodo             # download, verify, write manifest
```

Options: `--force` (re-download even if a file exists), `--retries N`, `--timeout SECONDS`,
`--record-id`, `--dest`, `--manifest`.

Behaviour:
- File names, sizes, checksums and download URLs come from the Zenodo REST API record, not from guessed URLs.
- Streaming downloads to `<name>.part`, then an atomic rename after the size and checksum match.
  Interrupted downloads resume with an HTTP `Range` request when the server supports it.
- Retries with back-off on network errors and HTTP 408/425/429/5xx; clear failures otherwise.
- An existing file is never overwritten blindly: if it verifies it is kept; if it does not match,
  the run fails and tells you (`--force` replaces it).
- No access token is used or sent. The User-Agent identifies the project and contains no personal data.
- Exit codes: `0` success, `1` one or more files failed (the manifest records the error), `2` metadata failure.
- Re-running is safe: verified files are kept and their original download timestamps are preserved.

### Manifest fields
Record: id, DOI, concept DOI, title, publication date, version (`null` and noted if Zenodo gives none),
URLs, retrieval timestamp, licence, creators, description. Each file: name, extension, size, Zenodo checksum,
independently calculated MD5 and SHA-256, `checksum_verified`, download URL, local path, status,
download/verify timestamps, error, notes, licence block and provenance block.

### Licence and provenance caveats
- Zenodo states one **record-level** licence (`cc-by-4.0`) and no file-level licences. The manifest says so
  explicitly and never copies the record licence into a file-specific field.
- The provenance block shows the nearby record text and a **keyword heuristic** for possible third-party
  origin (for example OpenStreetMap, US Embassy, Clarity, gridded population). It is context for a human
  reviewer, not proof. Every file is marked `review_required: true`, because the record does not document
  upstream sources or terms for any file.

## Stage 2: US Embassy PM2.5 (`process_embassy.py`)

Extracts, cleans and validates `bishkek_monitoring_us.embassy2019-2024.rar` from the Stage 1 download.

```powershell
backend\.venv\Scripts\python.exe -m pipeline.process_embassy
```

Steps: verify the archive checksum against `data/manifest/zenodo_12720883.json` → extract with the system
`tar` (bsdtar reads RAR; no extra dependency; the archive is never modified, no existing file is overwritten)
→ read the six yearly CSVs as text → clean → report → write outputs and `data/manifest/embassy_processing.json`.

| Location | Content |
|---|---|
| `data/interim/embassy/extracted/` | the six CSVs, unchanged |
| `data/interim/embassy/cleaned/embassy_pm25_cleaned_rows.parquet` | audit dataset: every source row, all original fields as `src_*` text, flags |
| `data/interim/embassy/reports/` | `embassy_validation_report.md`, `embassy_validation_stats.json`, gap, duplicate and irregular-day CSVs |
| `data/processed/embassy/embassy_pm25_hourly.parquet` / `.csv` | one row per hour: the primary analytical table |

All of `data/` except the manifests is git-ignored. `--skip-extract` reuses already extracted files.

Key rules (full list in the manifest `cleaning_rules` and the report):
- The measurement is `Raw Conc.` (µg/m³), never `NowCast Conc.`. `-999` is missing (NaN, never 0).
- `pm25_raw_ugm3` keeps negative values (`flag_neg_noise`); `pm25_model_ugm3` is clipped at 0 and filled only for
  QC `Valid` rows. `pm25_feature_interp_ugm3` fills gaps of at most 3 h for model *inputs only* (`is_imputed`);
  the target column is never interpolated.
- Timestamps are `Asia/Bishkek` (UTC+6) plus UTC; the original string is kept; nothing is shifted.
- Conflicting duplicate timestamps are excluded and flagged (`DUP_CONFLICT`), not guessed.
- High, spiking or abruptly dropping values are flagged (`flag_high_value`, `flag_local_spike`, `flag_local_drop`)
  and kept. The validation report documents a period of apparently inconsistent measurement behaviour; read its
  limitations section before using the data for modelling.

## Stage 3: Clarity 2021 sensors (`process_clarity.py`)

Reads `bishkek_monitoring_claritysensor_2021.xlsx` (read-only, checksum-verified against the Zenodo manifest) and
writes the extracted sheets, an auditable dataset, an hourly/daily/monthly analysis set, a device inventory, reports
and `data/manifest/clarity_processing.json`.

```powershell
backend\.venv\Scripts\python.exe -m pipeline.process_clarity
```

| Location | Content |
|---|---|
| `data/interim/clarity/extracted/` | every sheet as CSV, cell text unchanged (formula cells give their cached value) |
| `data/interim/clarity/cleaned/` | `clarity_audit_rows.parquet` (all worksheet rows, `src_*` originals, flags), `clarity_timestamp_normalized.parquet`, `clarity_device_inventory.parquet`, `clarity_device_coverage.parquet` |
| `data/interim/clarity/reports/` | validation report + stats, timestamp, device-coverage, spatial and Embassy-comparison reports |
| `data/processed/clarity/` | `clarity_pm25_hourly.parquet`/`.csv`, `clarity_daily.parquet`, `clarity_monthly.parquet`, `clarity_device_inventory.csv` |

Things to know:
- The workbook is read with `pipeline/xlsx_reader.py` (standard library only), which keeps each cell's stored text.
- Only 231,316 of the 242,142 data-sheet rows are measurements; the other 10,826 are blank rows below the Excel
  table and are kept in the audit data as `BLANK_ROW`.
- **The hour convention and time zone are not documented by the source.** They are configuration, with a
  provisional default (`hour_ending`, `Asia/Bishkek`, no extra shift) recorded in every output:
  `--hour-convention {hour_ending,hour_start}`, `--source-timezone {Asia/Bishkek,UTC}`, `--extra-shift-hours N`.
  No reading is ever shifted to match the Embassy series.
- An existing output whose content would change is never overwritten unless you pass `--allow-overwrite`
  (needed after changing the timestamp settings). Re-running with the same settings leaves every file unchanged.
- Calibration is not verified: the source paper reports "periodic calibrations" but no method or per-device
  records were found. Readings are used as supplied.
- `--skip-embassy` skips the Embassy comparison (otherwise `data/processed/embassy/` must exist and match its manifest).

## Stage 4: GIS, CAMx and emissions (`process_gis.py`)

Verifies and extracts the five Zenodo GIS archives, validates every spatial dataset and builds the integrated
0.01° grid. Needs the Stage 1 download only (not Stages 2-3).

```powershell
backend\.venv\Scripts\python.exe -m pipeline.process_gis
```

| Location | Content |
|---|---|
| `data/interim/gis/extracted/` | the five archives unpacked unchanged (116 files) |
| `data/interim/gis/validated/` | `gis_validation_checks.json` (all checks), `emissions_sector_summary.csv` |
| `data/interim/gis/reports/gis_validation_report.md` | the consolidated report |
| `data/processed/gis/` | `population_grid`, `road_density_grid`, `camx_pm25_grid`, `emissions_area_grid` (plain Parquet keyed by `cell_id`); `roads`, `emissions_point_sources`, `airwatch_spatial_grid` (GeoParquet); `administrative_boundaries`, `context_layers` (GeoJSON) |

Things to know:
- All outputs are WGS84 (EPSG:4326). Lengths and areas are computed in UTM 43N (EPSG:32643).
- The grid, population and road shapefiles **declare no CRS**; WGS84 is an assumption with the evidence recorded in the report and manifest.
- CAMx and emissions are **modelled**, population is an **estimate**, and the vintages differ (population 2000-2019, emissions 2018,
  CAMx year not stated). The integrated grid keeps each source's own date and never presents modelled values as measurements.
- The `.emi` emission grids already include the point sources; never add `emissions_point_sources` to them.
- Road density is road **length per area**, not traffic.
- The "adm0" file is the Kyrgyzstan country polygon clipped to the airshed, not Bishkek's districts.
- An existing output that would change is never overwritten unless you pass `--allow-overwrite`.

## Phase 2: ERA5 weather (`process_weather.py`)

```powershell
backend\.venv\Scripts\python.exe -m pipeline.process_weather            # downloads once, then uses the cache
backend\.venv\Scripts\python.exe -m pipeline.process_weather --refresh  # deliberately download again
```
Historical **ERA5 reanalysis** for Bishkek from the Open-Meteo Historical Weather API
(`https://archive-api.open-meteo.com/v1/archive`, `models=era5`). Reanalysis is modelled past weather on a ~0.25 degree grid:
not a forecast, not a local station record. One request covers the Embassy period (dates read from
`data/manifest/embassy_processing.json`); the response is cached unchanged in `data/raw/weather/` with a request record
and SHA-256, so the API is never called again unless `--refresh` is given.

- Variables: temperature_2m (°C), relative_humidity_2m (%), wind_speed_10m (m/s, requested explicitly; the API default is km/h),
  wind_direction_10m (°), surface_pressure (hPa), precipitation (mm, sum of the preceding hour), boundary_layer_height (m).
- Time: requested in UTC, converted to Asia/Bishkek. Missing values stay missing (no interpolation); returned units are checked.
- Outputs: `data/interim/weather/` (parsed table, validation report), `data/processed/weather/era5_hourly.parquet`,
  `data/manifest/weather_processing.json` (request, retrieval time, SHA-256, grid cell actually returned, units, coverage, limitations, licence).
- Licence: data CC BY 4.0 (attribute "Weather data by Open-Meteo.com"); the free API is for **non-commercial** use only.
- The default "best match" model and ERA5-Land are deliberately not used: the first blends models, the second lacks wind, pressure, precipitation and boundary-layer height.
- Known gap: boundary_layer_height is missing for 2024-01-01 onward in the ERA5 response (4,368 hours); it is reported, not filled.
- On some Windows machines Python's own certificate store rejected the server chain ("certificate has expired"); the downloader
  uses the `certifi` bundle when it is installed (verification is never disabled).

## Tests

From the repository root, the whole project in one command:

```powershell
backend\.venv\Scripts\python.exe -m pytest
```
A root `pytest.ini` (testpaths `backend/tests` and `pipeline/tests`, import paths `.` and `backend`) makes this work;
`backend/pytest.ini` and `pipeline/pytest.ini` still apply when you run a single suite
(`pytest backend`, `pytest pipeline/tests`). Pipeline tests use small synthetic fixtures and an in-memory fake
Zenodo server; they need no network and never touch the real dataset.
