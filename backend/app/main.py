from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import explorer, grid, health, layers, measurements, metadata, planner, stations, weather
from app.core.config import settings
from app.services.errors import BadQuery, DataUnavailable, NotFound


def create_app(static_dir: Path | None = None) -> FastAPI:
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

    _serve_frontend(app, static_dir or settings.static_dir)
    return app


def _serve_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built frontend from one origin with the API (Docker deploy). No-op without a build."""
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def _spa(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Unknown API path: /{path}", "available": []})
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        return FileResponse(index)  # client-side routes fall back to the app shell


app = create_app()
