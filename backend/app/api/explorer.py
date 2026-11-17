from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_store
from app.services import explorer as svc
from app.services import weather as wx
from app.services.data_store import DataStore

router = APIRouter(tags=["explorer"])


@router.get("/explorer")
def explorer(
    source: Optional[Literal["embassy", "clarity"]] = Query(None, description="Network. Defaults to the Embassy when no station_id is given."),
    station_id: Optional[str] = Query(None, description="Required for Clarity (device id). Embassy: 'us-embassy-bishkek'."),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to the start of the station's data."),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive; defaults to the end of the station's data."),
    interval: Literal["hourly", "daily", "monthly"] = Query("daily"),
    include_weather: bool = Query(True),
    weather_offset_hours: int = Query(0, description="Hourly only: shift the ERA5 time used for pairing. Default 0. Never auto-tuned."),
    variables: Optional[str] = Query(None, description="Comma-separated weather variables; default all."),
    store: DataStore = Depends(get_store),
):
    """Historical PM2.5 for ONE station with ERA5 weather alongside. Embassy and Clarity are never combined."""
    return svc.explorer(store, source, station_id, start_date, end_date, interval, include_weather, weather_offset_hours, variables)


@router.get("/explorer/summary")
def explorer_summary(
    source: Optional[Literal["embassy", "clarity"]] = Query(None),
    station_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    store: DataStore = Depends(get_store),
):
    """Statistics of VALID observations plus counts of excluded, suspect, flagged and imputed hours, monthly and seasonal summaries."""
    return svc.summary(store, source, station_id, start_date, end_date)


@router.get("/explorer/compare")
def explorer_compare(
    station_a: Optional[str] = Query(None, description="Station for period A (default: Embassy)."),
    start_a: Optional[str] = Query(None), end_a: Optional[str] = Query(None),
    station_b: Optional[str] = Query(None, description="Station for period B (default: same as A)."),
    start_b: Optional[str] = Query(None), end_b: Optional[str] = Query(None),
    interval: Literal["daily", "monthly"] = Query("daily"),
    store: DataStore = Depends(get_store),
):
    """Compare two periods and/or two stations; unequal coverage, different seasons and different networks are flagged explicitly."""
    return svc.compare(store, station_a, start_a, end_a, station_b, start_b, end_b, interval)
