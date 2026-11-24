"""Settings: CloudNativePG's database URL works with the installed driver."""

import pytest
from sqlalchemy import make_url

from larkspur_track.config import Settings, use_psycopg_driver

# Same shape as the "uri" key in CloudNativePG's <cluster>-app secret; the password is
# percent-encoded there, so "p%40ss" is the password "p@ss".
CNPG_URI = "postgresql://app:p%40ss@larkspur-db-rw:5432/app"
PSYCOPG_URI = "postgresql+psycopg://app:p%40ss@larkspur-db-rw:5432/app"
DECODED = "p@ss"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (CNPG_URI, PSYCOPG_URI),
        ("postgres://app:pw@db:5432/app", "postgresql+psycopg://app:pw@db:5432/app"),
        (PSYCOPG_URI, PSYCOPG_URI),
        ("sqlite://", "sqlite://"),
    ],
)
def test_use_psycopg_driver(url: str, expected: str) -> None:
    assert use_psycopg_driver(url) == expected


def test_settings_use_psycopg_for_cnpg_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", CNPG_URI)
    monkeypatch.setenv("TEST_DATABASE_URL", CNPG_URI)
    settings = Settings(_env_file=None)
    url = make_url(settings.database_url)
    assert url.get_dialect().driver == "psycopg"
    assert url.password == DECODED
    assert settings.test_database_url == PSYCOPG_URI


def test_settings_without_test_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", CNPG_URI)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert Settings(_env_file=None).test_database_url is None
