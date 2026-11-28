import base64
import contextlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy import select
from sqlalchemy.orm import Session

from larkspur_track.models import OutboxEvent
from larkspur_track.services import StepDetails, advance_shipment, create_shipment
from larkspur_track.sync import OutboxCollector, QueueSender, SendError, send_pending
from larkspur_track.workflow import ShipmentStatus

SAS = "sv=2026-04-06&si=sync-1&sig=secret%2Fsignature%3D"


class FakeSender:
    """Records messages; fails from the given call number on (counting from 1)."""

    def __init__(self, fail_from: int | None = None) -> None:
        self.bodies: list[dict] = []
        self.fail_from = fail_from
        self.calls = 0

    def send(self, body: str) -> None:
        self.calls += 1
        if self.fail_from is not None and self.calls >= self.fail_from:
            raise SendError("URLError: [Errno -3] Temporary failure in name resolution")
        self.bodies.append(json.loads(body))


def _factory(session: Session):  # noqa: ANN202
    return lambda: contextlib.nullcontext(session)


def _three_events(session: Session, details: StepDetails) -> None:
    shipment = create_shipment(
        session, tracking_number="LRK-SYNC-0001", consignee="Acme (fictional)", destination="Dock 2"
    )
    advance_shipment(session, shipment.id, ShipmentStatus.STORED, details=details)
    advance_shipment(session, shipment.id, ShipmentStatus.PICKED, details=details)


def _rows(session: Session) -> list[OutboxEvent]:
    session.expire_all()
    return list(session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


def test_sends_every_event_oldest_first_and_marks_them_sent(
    db_session: Session, step_details: StepDetails
) -> None:
    _three_events(db_session, step_details)
    sender = FakeSender()

    result = send_pending(_factory(db_session), sender, "larkspur-dev")

    rows = _rows(db_session)
    assert result.sent == 3 and result.error is None
    assert [b["event"]["event_id"] for b in sender.bodies] == [str(r.event_id) for r in rows]
    assert [b["event"]["to_status"] for b in sender.bodies] == ["received", "stored", "picked"]
    assert all(b["site"] == "larkspur-dev" and b["schema"] == 1 for b in sender.bodies)
    assert all(r.sent_at is not None and r.attempts == 1 and r.last_error is None for r in rows)
    assert send_pending(_factory(db_session), sender, "larkspur-dev").sent == 0


def test_a_failure_stops_the_round_and_keeps_the_order(
    db_session: Session, step_details: StepDetails
) -> None:
    _three_events(db_session, step_details)

    result = send_pending(_factory(db_session), FakeSender(fail_from=2), "larkspur-dev")

    rows = _rows(db_session)
    assert result.sent == 1
    assert result.error == "URLError: [Errno -3] Temporary failure in name resolution"
    assert [r.sent_at is not None for r in rows] == [True, False, False]
    assert [r.attempts for r in rows] == [1, 1, 0]
    assert rows[1].last_error == result.error

    # The link comes back: the next round starts with the event that failed.
    retry = FakeSender()
    assert send_pending(_factory(db_session), retry, "larkspur-dev").sent == 2
    rows = _rows(db_session)
    assert [b["event"]["event_id"] for b in retry.bodies] == [str(r.event_id) for r in rows[1:]]
    assert rows[1].attempts == 2 and rows[1].last_error is None


def test_limit_and_rate_limit(db_session: Session, step_details: StepDetails) -> None:
    _three_events(db_session, step_details)
    pauses: list[float] = []

    result = send_pending(
        _factory(db_session), FakeSender(), "s", limit=2, min_interval=0.1, sleep=pauses.append
    )

    assert result.sent == 2
    assert pauses == [0.1, 0.1]


def test_outbox_collector_reports_backlog(db_session: Session, step_details: StepDetails) -> None:
    registry = CollectorRegistry()
    registry.register(OutboxCollector(_factory(db_session)))
    assert registry.get_sample_value("larkspur_outbox_pending") == 0
    assert registry.get_sample_value("larkspur_outbox_oldest_pending_seconds") == 0

    _three_events(db_session, step_details)

    assert registry.get_sample_value("larkspur_outbox_pending") == 3
    assert registry.get_sample_value("larkspur_outbox_oldest_pending_seconds") >= 0


@pytest.fixture
def queue_server() -> Iterator[tuple[str, list[dict], list[int]]]:
    """A local stand-in for the Queue REST API. Returns its URL, the requests, and the status
    codes to answer with (201 when the list is empty)."""
    seen: list[dict] = []
    answers: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            seen.append({"path": self.path, "body": body, "version": self.headers["x-ms-version"]})
            self.send_response(answers.pop(0) if answers else 201)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/larkspur-events-dev", seen, answers
    finally:
        server.shutdown()
        server.server_close()


def test_queue_sender_posts_a_base64_message_that_never_expires(queue_server) -> None:  # noqa: ANN001
    url, seen, _ = queue_server

    QueueSender(url, "?" + SAS, timeout=5).send('{"hello": "world"}')

    [request] = seen
    parts = urlsplit(request["path"])
    assert parts.path == "/larkspur-events-dev/messages"
    query = parse_qs(parts.query)
    assert query["messagettl"] == ["-1"] and query["si"] == ["sync-1"]
    assert request["version"] == "2023-11-03"
    text = (
        request["body"]
        .removeprefix("<QueueMessage><MessageText>")
        .removesuffix("</MessageText></QueueMessage>")
    )
    assert base64.b64decode(text).decode() == '{"hello": "world"}'


def test_queue_sender_errors_never_contain_the_token(queue_server) -> None:  # noqa: ANN001
    url, _, answers = queue_server
    answers.append(403)

    with pytest.raises(SendError) as refused:
        QueueSender(url, SAS, timeout=5).send("x")
    with pytest.raises(SendError) as unreachable:
        QueueSender("http://127.0.0.1:9/nowhere", SAS, timeout=2).send("x")

    assert str(refused.value).startswith("HTTP 403")
    for error in (refused.value, unreachable.value):
        assert "secret" not in str(error) and "sig=" not in str(error)
