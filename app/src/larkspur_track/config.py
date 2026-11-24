"""Settings from environment variables; app/.env supplies them for local development."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Environment variables override values in .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    test_database_url: str | None = None
    display_timezone: str = "America/New_York"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
