"""Shared fixtures. Database tests use TEST_DATABASE_URL (see docs/runbooks/local-dev.md)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from larkspur_track.config import Settings, get_settings
from larkspur_track.db import get_engine, get_session
from larkspur_track.main import app

APP_DIR = Path(__file__).resolve().parents[1]


def _reset_database_caches() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = Settings().test_database_url
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set: run app/scripts/setup-dev-db.sh in WSL")
    return url


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(APP_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(APP_DIR / "migrations"))
    return config


@pytest.fixture
def use_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Point the app at a given database URL for one test, then restore."""

    def _use(url: str) -> None:
        _reset_database_caches()
        monkeypatch.setenv("DATABASE_URL", url)

    yield _use
    _reset_database_caches()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str, alembic_config: Config) -> str:
    """The test database upgraded to the latest migration, once per test run."""
    with pytest.MonkeyPatch.context() as patch:
        _reset_database_caches()
        patch.setenv("DATABASE_URL", test_database_url)
        command.upgrade(alembic_config, "head")
        _reset_database_caches()
    return test_database_url


@pytest.fixture
def db_session(migrated_database: str) -> Iterator[Session]:
    """A session inside a transaction that is rolled back after the test, leaving no data."""
    engine = create_engine(migrated_database)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """The app, using the test session that is rolled back after each test."""
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
