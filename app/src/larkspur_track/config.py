"""Settings from environment variables; app/.env supplies them for local development."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SQLAlchemy reads a URL without a driver (postgresql://) as psycopg2, which is not installed.
# CloudNativePG's generated app secret uses that form, so such URLs are switched to psycopg
# (version 3). URLs that already name a driver are left unchanged.
_PLAIN_SCHEMES = ("postgresql://", "postgres://")
_PSYCOPG_SCHEME = "postgresql+psycopg://"


def use_psycopg_driver(url: str) -> str:
    """Return the URL with the psycopg driver when it names no driver."""
    for scheme in _PLAIN_SCHEMES:
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url.removeprefix(scheme)
    return url


class Settings(BaseSettings):
    """Application settings. Environment variables override values in .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    test_database_url: str | None = None
    display_timezone: str = "America/New_York"
    # Seconds to wait for PostgreSQL to accept a connection before giving up.
    database_connect_timeout: int = 3

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        return None if value is None else use_psycopg_driver(value)


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
