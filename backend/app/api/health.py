import sys
from datetime import datetime, timezone

from fastapi import APIRouter

from app import __version__
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness check. Reports service status only; it returns no air-quality data."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "python": sys.version.split()[0],
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
