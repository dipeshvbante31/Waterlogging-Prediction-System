"""Environment-based configuration (12-factor). No secrets are hardcoded."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"  # development | test | production
    log_level: str = "INFO"
    log_json: bool = False

    # Region
    city: str = "mumbai"
    city_config_path: str | None = None
    city_bbox: str | None = None            # "min_lat,min_lon,max_lat,max_lon" override of the profile

    # Database: SQLite for local dev/tests, PostgreSQL via DATABASE_URL in deployments.
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"

    # Artifact storage (datasets + trained models) behind a storage abstraction.
    storage_backend: str = "local"
    storage_path: str = str(BASE_DIR / "storage")

    # Upload limits
    max_upload_mb: int = 20
    max_csv_rows: int = 200_000

    # Retention
    upload_retention_hours: int = 24
    job_stale_minutes: int = 120
    history_retention_days: int = 90

    # Weather provider (public, no key required)
    weather_provider: str = "open-meteo"
    weather_timeout_s: float = 8.0
    weather_max_retries: int = 2
    weather_cache_ttl_s: int = 600
    weather_stale_max_s: int = 6 * 3600
    weather_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    weather_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    weather_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    weather_geocoding_country: str = "IN"   # ISO-3166 alpha-2 filter for place search; empty disables
    # Only needed for Open-Meteo's commercial customer API. Sent ONLY to customer-api.open-meteo.com hosts.
    open_meteo_api_key: str | None = None
    cors_allowed_origins: str = ""          # comma-separated explicit origins; empty = same-origin only
    allow_sqlite_in_production: bool = False

    secret_key: str = Field(default="change-me-in-production")
    admin_token: str | None = None          # protects train/activate/delete/cleanup; REQUIRED in production
    rate_limit_per_min: int = 60            # per-IP, per-process; 0 disables
    cleanup_interval_minutes: int = 30

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


class ConfigError(Exception):
    pass


def production_problems(s: Settings) -> list[str]:
    """Startup validation for APP_ENV=production. Returns human-readable problems."""
    p = []
    if not s.admin_token or len(s.admin_token) < 16:
        p.append("ADMIN_TOKEN must be set to a random value of at least 16 characters.")
    if s.secret_key == "change-me-in-production":
        p.append("SECRET_KEY must be changed from the default.")
    if s.database_url.startswith("sqlite") and not s.allow_sqlite_in_production:
        p.append("DATABASE_URL must be PostgreSQL in production (or set ALLOW_SQLITE_IN_PRODUCTION=true for a throwaway demo).")
    if "*" in s.cors_allowed_origins:
        p.append("CORS_ALLOWED_ORIGINS must list explicit origins, not '*'.")
    return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
