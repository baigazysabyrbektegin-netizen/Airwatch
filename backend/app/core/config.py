from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/app/core/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings. Override with environment variables prefixed AIRWATCH_
    or a local, git-ignored `.env` file. Never put secrets in source control."""

    model_config = SettingsConfigDict(env_prefix="AIRWATCH_", env_file=".env", extra="ignore")

    app_name: str = "AirWatch API"
    data_dir: Path = REPO_ROOT / "data"
    # Origins allowed to call the API from a browser (Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Built frontend (npm run build). Served at / when present, e.g. in the Docker image; absent in Vite development.
    static_dir: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def interim_dir(self) -> Path:
        return self.data_dir / "interim"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifest"


settings = Settings()
