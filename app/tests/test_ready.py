from collections.abc import Callable
from types import SimpleNamespace
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from larkspur_track import db, main
from larkspur_track.main import app


class _UnreachableEngine:
    """Stands in for an engine whose database cannot be reached."""

    def connect(self) -> NoReturn:
        raise OperationalError("SELECT 1", None, Exception("connection refused"))


def test_readyz_ok_when_database_answers(
    use_database: Callable[[str], None], test_database_url: str
) -> None:
    use_database(test_database_url)

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_503_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "get_engine", lambda: _UnreachableEngine())

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503


def test_engine_gives_up_connecting_after_the_timeout(
    monkeypatch: pytest.MonkeyPatch, use_database: Callable[[str], None]
) -> None:
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(dispose=lambda: None)

    use_database("postgresql+psycopg://user:secret@127.0.0.1:5432/app")
    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    db.get_engine()

    assert captured["connect_args"] == {"connect_timeout": 3}
