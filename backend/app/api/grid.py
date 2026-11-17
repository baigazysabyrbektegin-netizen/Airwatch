from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.api.deps import get_store
from app.services import geo, grid as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["grid"])


@router.get("/grid")
def grid(
    camx_month: str = Query("annual", description="annual or jan..dec: which modelled CAMx value is returned as camx_pm25_ugm3."),
    fields: Optional[str] = Query(None, description="Comma-separated grid columns (default: a compact set). See 'metadata.fields'."),
    min_population: Optional[float] = Query(None, ge=0, description="Minimum 2019 population per cell."),
    max_population: Optional[float] = Query(None, ge=0),
    min_emissions: Optional[float] = Query(None, ge=0, description="Minimum 2018 PM2.5 emissions (t/cell/yr)."),
    min_road_density: Optional[float] = Query(None, ge=0, description="Minimum total road length density (km/km²)."),
    min_camx_pm25: Optional[float] = Query(None, ge=0),
    max_camx_pm25: Optional[float] = Query(None, ge=0),
    bbox: Optional[str] = Query(None, description="minlon,minlat,maxlon,maxlat"),
    geometry: bool = Query(True, description="false returns properties only (no polygons)."),
    limit: Optional[int] = Query(None, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    store: DataStore = Depends(get_store),
):
    """The integrated 2,000-cell grid as GeoJSON. Each field keeps its own data kind and vintage (see metadata.fields)."""
    fc = svc.query(store, camx_month=camx_month, fields=fields, min_population=min_population, max_population=max_population, min_emissions=min_emissions,
                   min_road_density=min_road_density, min_camx_pm25=min_camx_pm25, max_camx_pm25=max_camx_pm25, bbox=bbox, include_geometry=geometry, limit=limit, offset=offset)
    return JSONResponse(fc, media_type=geo.GEOJSON_MEDIA_TYPE)
