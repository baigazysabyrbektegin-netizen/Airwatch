from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_store
from app.services import weather as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["weather"])


@router.get("/weather")
def weather(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD (Asia/Bishkek calendar day)."),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive."),
    variables: Optional[str] = Query(None, description="Comma-separated subset of: " + ", ".join(svc.VARIABLES) + ". Default: all."),
    interval: Literal["hourly", "daily", "monthly"] = Query("daily"),
    limit: int = Query(5000, ge=1, le=svc.HOURLY_ROW_CAP),
    offset: int = Query(0, ge=0),
    store: DataStore = Depends(get_store),
):
    """Historical ERA5 reanalysis weather for Bishkek (from the processed cache; Open-Meteo is never called per request).

    ERA5 is a model-based reconstruction of past weather on a ~25 km grid: not a forecast, not a local station, not real time."""
    return svc.query(store, start_date, end_date, variables, interval, limit, offset)
