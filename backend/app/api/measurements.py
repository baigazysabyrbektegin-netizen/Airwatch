from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_store
from app.schemas import MeasurementsResponse
from app.services import measurements as svc
from app.services.data_store import DataStore

router = APIRouter(tags=["measurements"])


@router.get("/measurements", response_model=MeasurementsResponse)
def measurements(
    station: str = Query(..., description="Station id, e.g. a Clarity device id or 'us-embassy-bishkek'. One station per request."),
    start: Optional[str] = Query(None, description="ISO date or datetime (Asia/Bishkek local time if no zone is given)."),
    end: Optional[str] = Query(None, description="ISO date or datetime; a date-only value includes that whole day."),
    interval: Literal["hourly", "daily", "monthly"] = Query("hourly"),
    limit: int = Query(2000, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    include_gaps: bool = Query(False, description="Embassy hourly only: also return hours with no source row (values null)."),
    store: DataStore = Depends(get_store),
):
    """Historical PM2.5 for one station on that station's own timestamps.

    Series from different stations are deliberately not merged: the Clarity/Embassy 1-hour offset is unresolved."""
    return svc.query(store, station, start, end, interval, limit, offset, include_gaps)
