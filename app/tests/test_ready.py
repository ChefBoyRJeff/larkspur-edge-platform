from collections.abc import Callable

from fastapi.testclient import TestClient

from larkspur_track.main import app

UNREACHABLE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


def test_readyz_ok_when_database_answers(
    use_database: Callable[[str], None], test_database_url: str
) -> None:
    use_database(test_database_url)

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_503_when_database_unreachable(use_database: Callable[[str], None]) -> None:
    use_database(UNREACHABLE)

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
