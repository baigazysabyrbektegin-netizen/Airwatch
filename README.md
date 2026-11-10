# AirWatch AI

Bishkek Urban Air Quality Research & Intelligent Sensor Planning Platform.

**Status: project foundation only.** No air-quality data is loaded or displayed yet.

## Stack
- Frontend: React, TypeScript, Vite, Tailwind CSS, MapLibre GL JS, Recharts
- Backend: Python 3.13, FastAPI
- Data processing: pandas, GeoPandas, PyArrow (CSV, GeoJSON and Parquet files)
- ML (added later): scikit-learn, XGBoost

## Layout
```
backend/    FastAPI app (app/), tests (tests/), requirements.in, requirements.txt
frontend/   Vite React + TypeScript app
pipeline/   Reproducible data-processing scripts (none yet)
data/       raw/ (never committed), interim/, processed/, manifest/ (tracked provenance)
docs/       Decisions and design notes
notebooks/  Exploratory work
```

## Run the backend
From the repository root (Windows PowerShell):
```powershell
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```
Health check: <http://127.0.0.1:8000/api/health> · Interactive docs: <http://127.0.0.1:8000/docs>

Run the tests: `cd backend; .venv\Scripts\python.exe -m pytest`

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
Open <http://localhost:5173>. Requests to `/api/*` are proxied to the backend on port 8000.
Other scripts: `npm test`, `npm run lint`, `npm run build`.

## Data rules
- Raw datasets are downloaded by scripts into `data/raw/` and are **git-ignored**.
- Every dataset is described in `data/manifest/` (source URL, retrieval date, checksum, licence, attribution).
- Measured, modelled, reanalysis, satellite and static data are always labelled and never mixed silently.
- See [docs/DECISIONS.md](docs/DECISIONS.md) for the provisional defaults and wording rules.

## Licences and attribution
Data sources carry their own licences (for example CC BY 4.0, ODbL). Attribution is required and
will be included with every export. Details will live in the manifest.
