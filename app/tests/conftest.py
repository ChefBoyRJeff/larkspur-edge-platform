"""Shared fixtures. Database tests use TEST_DATABASE_URL (see docs/runbooks/local-dev.md)."""

from collections.abc import Callable, Iterator

import pytest

from larkspur_track.config import Settings, get_settings
from larkspur_track.db import get_engine


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


@pytest.fixture
def use_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Point the app at a given database URL for one test, then restore."""

    def _use(url: str) -> None:
        _reset_database_caches()
        monkeypatch.setenv("DATABASE_URL", url)

    yield _use
    _reset_database_caches()
