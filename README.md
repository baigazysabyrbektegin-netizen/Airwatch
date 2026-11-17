# AirWatch

Bishkek Urban Air Quality Research & Intelligent Sensor Planning Platform.

**Status:** The Research Map, Data & Weather Explorer, Sensor Planner and Research Workspace are available locally.

## Stack
- Frontend: React, TypeScript, Vite, Tailwind CSS, MapLibre GL JS, Recharts
- Backend: Python 3.13, FastAPI
- Data processing: pandas, GeoPandas, PyArrow (CSV, GeoJSON and Parquet files)

## Layout
```
backend/    FastAPI app (app/), tests (tests/), requirements.in, requirements.txt
frontend/   Vite React + TypeScript app
pipeline/   Reproducible data-processing scripts
data/       raw/ (never committed), interim/, processed/, manifest/ (tracked provenance)
docs/       Decisions and design notes
notebooks/  Exploratory work
```

## Clone and run from a fresh checkout

Requirements:

- Git
- Python 3.13
- Node.js and npm
- Windows `tar` (included in current Windows versions; used to extract the source RAR archive)
- Internet access for the initial dataset and weather downloads

Clone the repository and create the backend environment:

```powershell
git clone https://github.com/baigazysabyrbektegin-netizen/Airwatch.git
cd Airwatch
py -3.13 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm ci
cd ..
```

The Git repository intentionally does not contain the large raw and processed datasets. Build them once,
from the repository root, in this order:

```powershell
$env:PYTHONPATH = "."
backend\.venv\Scripts\python.exe -m pipeline.ingest_zenodo
backend\.venv\Scripts\python.exe -m pipeline.process_embassy
backend\.venv\Scripts\python.exe -m pipeline.process_clarity
backend\.venv\Scripts\python.exe -m pipeline.process_gis
backend\.venv\Scripts\python.exe -m pipeline.process_weather
```

The ingestion and processing steps can take time and require several gigabytes of free disk space.
They are safe to rerun; verified downloads and unchanged outputs are reused. The weather step requires
internet access. If you only need to verify that the application starts, you can skip these steps, but
data pages will report that their datasets are unavailable.

Open two PowerShell windows. In the first, start the backend from the repository root:

```powershell
$env:PYTHONPATH = "."
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

In the second, start the frontend:

```powershell
cd frontend
npm run dev
```

Open the URL printed by Vite and check the backend at
<http://127.0.0.1:8000/api/health>.

## Run the backend
From the repository root (Windows PowerShell), so the root-level `pipeline/` package is importable:
```powershell
$env:PYTHONPATH = "."
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8000
```
Health check: <http://127.0.0.1:8000/api/health> · Interactive docs: <http://127.0.0.1:8000/docs>

The API reads only `data/processed/` and `data/manifest/` (never `data/raw/`). Endpoints:
`/api/health`, `/api/stations`, `/api/measurements`, `/api/grid`, `/api/layers`, `/api/layers/{id}`, `/api/metadata`,
`/api/weather`, `/api/explorer`, `/api/explorer/summary`, `/api/explorer/compare`.
The Sensor Planner adds `/api/planner/overview`, `/api/planner/candidates`, `/api/planner/coverage` and
`POST /api/planner/recalculate`.

The Sensor Planner uses active Clarity coordinates, the integrated grid cells with positive overlap with the available
Kyrgyzstan polygons, and documented 2019 population, historical CAMx, 2018 emissions and road/context fields.
Coverage radius, candidate separation and exposure/source weights are planning assumptions and configurable; grid
centroids are approximate investigation locations, not confirmed installation sites.

The Research Workspace is available from the frontend navigation. It reuses the Explorer and Sensor Planner APIs,
saves only lightweight comparison settings in browser-local storage, and exports station, period and candidate
results with provenance notes. Saved workspaces are specific to the browser/device where they were created.

The Data & Weather Explorer needs the ERA5 weather file, created once (needs internet, then cached):
`$env:PYTHONPATH = "."; .\backend\.venv\Scripts\python.exe -m pipeline.process_weather` from the repository root. Without it the explorer still shows PM2.5 and says that weather is unavailable.
If processed files are missing the API answers with a clear 503 naming the file and the pipeline step that creates it.

Run all tests (backend and pipeline) from the repository root:
`.\backend\.venv\Scripts\python.exe -m pytest`. A single suite: `pytest backend` or `pytest pipeline/tests`.

First-time setup of the virtual environment (Python 3.13):
```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the frontend
```powershell
cd frontend
npm install
npm run dev
```
Open the address Vite prints (normally <http://localhost:5173>; it picks the next free port if that is taken).
Requests to `/api/*` are proxied to the backend on port 8000, so start the backend first.
The basemap comes from OpenFreeMap (no key needed); without internet the data layers still draw on a plain background.
Other scripts: `npm test`, `npm run lint`, `npm run build`.

## Deploy (Render, one Docker image)
The image contains the API, the processed data and the built frontend; FastAPI serves the frontend at `/` when
`frontend/dist` exists, so the site and `/api` share one origin. The pipeline is **not** run on the server.

1. Build the data locally (see above), then pack it:
   `backend\.venv\Scripts\python.exe scripts\pack_data.py` — prints the archive path and its SHA-256.
2. Upload the archive to a GitHub Release (for example tag `data-v1`), or
   `gh release create data-v1 dist-datairwatch-data-YYYYMMDD.tar.gz`.
3. Put the asset URL and SHA-256 into `render.yaml` (`DATA_URL`, `DATA_SHA256`), commit and push.
4. On render.com: **New → Blueprint**, pick this repository. Render passes the env vars to the Docker build
   as build args; the build fails if the checksum does not match.

Check locally first, with the free-plan memory limit:
```powershell
docker build --build-arg DATA_URL=<asset url> --build-arg DATA_SHA256=<sha256> -t airwatch .
docker run --rm -m 512m -p 8000:8000 airwatch
```
Open <http://localhost:8000> and watch `docker stats`. The free plan sleeps after 15 minutes without visits; the
first request afterwards takes about a minute.

## Data rules
- Raw datasets are downloaded by scripts into `data/raw/` and are **git-ignored**.
- Every dataset is described in `data/manifest/` (source URL, retrieval date, checksum, licence, attribution).
- Measured, modelled, reanalysis, satellite and static data are always labelled and never mixed silently.
- See [docs/DECISIONS.md](docs/DECISIONS.md) for the provisional defaults and wording rules.

## Licences and attribution
Data sources carry their own licences (for example CC BY 4.0, ODbL). Attribution is required and
will be included with every export. Details will live in the manifest.
