"""Prometheus metrics: request counts by route template, sign-ins, and shipments by status."""

import http.client
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from wsgiref.simple_server import WSGIServer

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, CollectorRegistry
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from conftest import PHRASE, MakeUser, SignedInClient
from larkspur_track.main import create_app
from larkspur_track.metrics import UNMATCHED, ShipmentCollector, start_metrics_server
from larkspur_track.services import create_shipment
from larkspur_track.workflow import ShipmentStatus

FORM = {
    "tracking_number": "LRK-METRICS-0001",
    "consignee": "Acme Retail (fictional)",
    "destination": "Dock 4, Greensboro NC",
}


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _requests(method: str, route: str, status: str) -> float:
    return _value("larkspur_http_requests_total", method=method, route=route, status=status)


def test_requests_are_counted_by_route_template(client: SignedInClient) -> None:
    location = client.post("/shipments", data=FORM).headers["location"]
    route = "/shipments/{shipment_id}"
    before = _requests("GET", route, "200")
    timed_before = _value("larkspur_http_request_duration_seconds_count", method="GET", route=route)

    client.get(location)
    client.get(location)

    assert _requests("GET", route, "200") == before + 2
    assert (
        _value("larkspur_http_request_duration_seconds_count", method="GET", route=route)
        == timed_before + 2
    )


def test_redirects_and_unknown_paths_get_their_own_labels(
    client: SignedInClient, anonymous_client: TestClient
) -> None:
    posted = _requests("POST", "/shipments", "303")
    unmatched = _requests("GET", UNMATCHED, "404")
    signed_out = _requests("GET", "/shipments", "303")

    client.post("/shipments", data=FORM)
    anonymous_client.get("/wp-login.php")
    anonymous_client.get("/shipments")

    assert _requests("POST", "/shipments", "303") == posted + 1
    assert _requests("GET", UNMATCHED, "404") == unmatched + 1
    assert _requests("GET", "/shipments", "303") == signed_out + 1


def test_sign_ins_are_counted_by_result(anonymous_client: TestClient, make_user: MakeUser) -> None:
    make_user()
    success = _value("larkspur_sign_ins_total", result="success")
    failure = _value("larkspur_sign_ins_total", result="failure")

    anonymous_client.post("/sign-in", data={"username": "sam.supervisor", "password": "wrong"})
    anonymous_client.post("/sign-in", data={"username": "sam.supervisor", "password": PHRASE})

    assert _value("larkspur_sign_ins_total", result="failure") == failure + 1
    assert _value("larkspur_sign_ins_total", result="success") == success + 1


def test_metrics_are_not_served_on_the_web_port(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/metrics").status_code == 404


def test_shipments_are_counted_by_status(db_session: Session) -> None:
    for number in ("LRK-M-1", "LRK-M-2"):
        create_shipment(db_session, tracking_number=number, consignee="Acme", destination="Dock 4")

    [family] = ShipmentCollector(lambda: nullcontext(db_session)).collect()

    counts = {sample.labels["status"]: sample.value for sample in family.samples}
    assert counts == {status.value: 0 for status in ShipmentStatus} | {"received": 2}


def test_database_errors_skip_the_shipment_metric() -> None:
    def broken() -> nullcontext[Session]:
        raise OperationalError("SELECT 1", {}, Exception("database is down"))

    assert list(ShipmentCollector(broken).collect()) == []


@pytest.fixture
def metrics_server(db_session: Session) -> Iterator[WSGIServer]:
    registry = CollectorRegistry()
    server = start_metrics_server(0, lambda: nullcontext(db_session), registry=registry)
    yield server
    server.shutdown()
    server.server_close()


def test_metrics_server_serves_the_shipment_metric(
    metrics_server: WSGIServer, db_session: Session
) -> None:
    create_shipment(db_session, tracking_number="LRK-M-3", consignee="Acme", destination="Dock 4")
    connection = http.client.HTTPConnection("127.0.0.1", metrics_server.server_port, timeout=5)

    connection.request("GET", "/metrics")
    body = connection.getresponse().read().decode()

    assert 'larkspur_shipments{status="received"} 1.0' in body


def test_app_starts_without_database_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_settings: None
) -> None:
    """The image smoke test runs the container without DATABASE_URL and expects /healthz."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("METRICS_PORT", raising=False)

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
