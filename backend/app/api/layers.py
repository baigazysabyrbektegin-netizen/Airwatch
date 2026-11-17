from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.api.deps import get_store
from app.schemas import LayersResponse
from app.services import geo, layers as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["layers"])


@router.get("/layers", response_model=LayersResponse)
def list_layers(store: DataStore = Depends(get_store)):
    """The map layer catalogue: data kind, vintage, units, limitations and availability for every layer."""
    cat = svc.catalogue(store)
    return {"count": len(cat), "layers": cat, "notes": ["Every layer is historical or modelled; none describes current conditions.",
                                                        "A layer is 'available' only if its processed file exists."]}


@router.get("/layers/{layer_id}")
def layer_data(
    layer_id: str,
    groups: Optional[str] = Query(None, description="roads only: comma-separated road groups, or 'all' (default primary,secondary,tertiary)."),
    layers: Optional[str] = Query(None, description="context_features only: comma-separated context layer keys."),
    bbox: Optional[str] = Query(None, description="minlon,minlat,maxlon,maxlat"),
    camx_month: str = Query("annual", description="camx_pm25 only: annual or jan..dec."),
    include_no_data: bool = Query(False, description="stations only: also devices without readings."),
    store: DataStore = Depends(get_store),
):
    """GeoJSON for one layer (see /api/layers for ids)."""
    fc = svc.layer_data(store, layer_id, groups=groups, layers=layers, bbox=bbox, camx_month=camx_month, include_no_data=include_no_data)
    return JSONResponse(fc, media_type=geo.GEOJSON_MEDIA_TYPE)
