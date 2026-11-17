from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import explorer, grid, health, layers, measurements, metadata, planner, stations, weather
from app.core.config import settings
from app.services.errors import BadQuery, DataUnavailable, NotFound


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Backend for AirWatch: Bishkek urban air quality research and sensor planning. "
                    "All data are historical measurements, models or estimates read from the processed datasets.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    for router in (health.router, stations.router, measurements.router, grid.router, layers.router, metadata.router, weather.router, explorer.router, planner.router):
        app.include_router(router, prefix="/api")

    @app.exception_handler(DataUnavailable)
    async def _data_unavailable(_: Request, exc: DataUnavailable):
        return JSONResponse(status_code=503, content={"error": "data_unavailable", "message": f"Required data is not available: {exc.file} ({exc.reason}).",
                                                      "file": exc.file, "hint": exc.hint})

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": exc.message, "available": exc.available})

    @app.exception_handler(BadQuery)
    async def _bad_query(_: Request, exc: BadQuery):
        return JSONResponse(status_code=422, content={"error": "invalid_query", "message": exc.message, "allowed": exc.allowed})

    return app


app = create_app()
