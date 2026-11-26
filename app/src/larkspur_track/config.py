"""Settings from environment variables; app/.env supplies them for local development."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
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
    # Release shown in the page footer; the Helm chart sets it to the deployed image tag.
    app_release: str = "local"
    # Browsers keep Secure cookies only from HTTPS sites. Local development over
    # http://127.0.0.1 sets SESSION_COOKIE_SECURE=false in app/.env (see scripts/setup-dev-db.sh).
    session_cookie_secure: bool = True

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        return None if value is None else use_psycopg_driver(value)


class MetricsSettings(BaseSettings):
    """Read at startup, apart from Settings, so the app starts (and /healthz answers) even
    without DATABASE_URL, as in the image smoke test."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Port for Prometheus metrics; 0 turns the metrics server off (the default for local runs).
    metrics_port: int = 0


class SyncSettings(BaseSettings):
    """Settings for the sync worker (larkspur-track-sync); the web app never reads them."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # The environment's queue, e.g. https://<account>.queue.core.windows.net/larkspur-events-dev,
    # and a SAS token that may only add messages to it (azure/integration/new-sync-credential.ps1).
    azure_queue_url: str
    azure_queue_sas: SecretStr
    # Sent with every event so the receiver knows which site and environment it came from.
    sync_site: str = "local"
    # Pause between rounds when the outbox is empty, and the longest pause after failures.
    sync_interval_seconds: float = 5
    sync_max_backoff_seconds: float = 300
    sync_request_timeout_seconds: float = 10
    # A runaway loop can't send more than this, which caps the queue's cost.
    sync_max_sends_per_minute: int = 600
    metrics_port: int = 0


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
