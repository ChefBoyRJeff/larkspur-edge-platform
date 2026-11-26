"""The sync worker: sends outbox events to the environment's Azure Storage Queue (ADR-0009).

Runs as its own Deployment (larkspur-track-sync, one replica). Each round takes the oldest
unsent event, sends it and stamps sent_at, until the outbox is empty or a send fails. When the
internet is down, sends fail and events wait in the outbox, in order; the worker retries with a
growing pause (up to sync_max_backoff_seconds) and catches up when the link returns.

Delivery is at least once: if the worker stops after a send but before its commit, the event is
sent again with the same event_id, which the receiver uses to drop the duplicate.
"""

import base64
import json
import logging
import signal
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, start_http_server
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from larkspur_track.config import SyncSettings
from larkspur_track.models import OutboxEvent

log = logging.getLogger("larkspur_track.sync")

SCHEMA_VERSION = 1
SENT = Counter("larkspur_sync_sent", "Outbox events sent to Azure.")
FAILURES = Counter("larkspur_sync_send_failures", "Failed attempts to send an outbox event.")
LAST_SUCCESS = Gauge("larkspur_sync_last_success_timestamp_seconds", "When an event was last sent.")
LAST_ROUND = Gauge(
    "larkspur_sync_last_round_timestamp_seconds", "When the worker last ran a round."
)


class SendError(Exception):
    """A send failed. The message never contains the SAS token."""


class QueueSender:
    """Adds messages to one Azure Storage Queue through its REST API, with a send-only SAS."""

    def __init__(self, queue_url: str, sas: str, timeout: float) -> None:
        self.queue_url = queue_url.rstrip("/")
        self._sas = sas.lstrip("?")
        self.timeout = timeout

    def _clean(self, text: str) -> str:
        return text.replace(self._sas, "<sas>") if self._sas else text

    def send(self, body: str) -> None:
        text = base64.b64encode(body.encode()).decode()
        data = f"<QueueMessage><MessageText>{text}</MessageText></QueueMessage>".encode()
        # messagettl=-1: messages never expire, so a receiver that is down a while loses nothing.
        request = urllib.request.Request(  # noqa: S310 (the URL comes from settings, not users)
            f"{self.queue_url}/messages?messagettl=-1&{self._sas}",
            data=data,
            method="POST",
            headers={"x-ms-version": "2023-11-03", "Content-Type": "application/xml"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                status = response.status
        except urllib.error.HTTPError as error:
            error.close()
            raise SendError(f"HTTP {error.code} {self._clean(str(error.reason))}") from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise SendError(f"{type(error).__name__}: {self._clean(str(reason))}") from None
        if status != 201:
            raise SendError(f"HTTP {status}")


def envelope(row: OutboxEvent, site: str) -> str:
    """The queue message: which site sent it, and the event itself."""
    return json.dumps({"schema": SCHEMA_VERSION, "site": site, "event": row.payload})


@dataclass
class RoundResult:
    sent: int
    error: str | None


SessionFactory = Callable[[], AbstractContextManager[Session]]


def send_pending(
    session_factory: SessionFactory,
    sender: QueueSender,
    site: str,
    *,
    limit: int = 100,
    min_interval: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> RoundResult:
    """Send unsent events oldest first, one transaction each; stop at the first failure."""
    sent = 0
    while sent < limit:
        with session_factory() as session:
            row = session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.sent_at.is_(None))
                .order_by(OutboxEvent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                return RoundResult(sent, None)
            row.attempts += 1
            try:
                sender.send(envelope(row, site))
            except SendError as error:
                row.last_error = str(error)[:300]
                session.commit()
                FAILURES.inc()
                return RoundResult(sent, row.last_error)
            row.sent_at = datetime.now(UTC)
            row.last_error = None
            session.commit()
        sent += 1
        SENT.inc()
        LAST_SUCCESS.set_to_current_time()
        if min_interval:
            sleep(min_interval)
    return RoundResult(sent, None)


class OutboxCollector(Collector):
    """Unsent events and the age of the oldest, read from the database at each scrape."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    def collect(self) -> Iterator[Metric]:
        try:
            with self.session_factory() as session:
                count, oldest = session.execute(
                    select(func.count(), func.min(OutboxEvent.created_at)).where(
                        OutboxEvent.sent_at.is_(None)
                    )
                ).one()
        except SQLAlchemyError:
            return
        pending = GaugeMetricFamily("larkspur_outbox_pending", "Outbox events not yet sent.")
        pending.add_metric([], count)
        yield pending
        age = GaugeMetricFamily(
            "larkspur_outbox_oldest_pending_seconds", "Age of the oldest unsent event (0 if none)."
        )
        age.add_metric([], 0 if oldest is None else (datetime.now(UTC) - oldest).total_seconds())
        yield age


def run(
    settings: SyncSettings,
    session_factory: SessionFactory,
    stop: threading.Event,
    registry: CollectorRegistry = REGISTRY,
) -> None:
    """Send rounds until stop is set."""
    sender = QueueSender(
        settings.azure_queue_url,
        settings.azure_queue_sas.get_secret_value(),
        settings.sync_request_timeout_seconds,
    )
    if settings.metrics_port:
        registry.register(OutboxCollector(session_factory))
        start_http_server(settings.metrics_port, registry=registry)
    min_interval = 60 / settings.sync_max_sends_per_minute
    pause = settings.sync_interval_seconds
    failing = False
    log.info("sync worker started for site %s", settings.sync_site)
    while not stop.is_set():
        try:
            result = send_pending(
                session_factory, sender, settings.sync_site, min_interval=min_interval
            )
        except SQLAlchemyError as error:
            result = RoundResult(0, f"database: {type(error).__name__}")
        LAST_ROUND.set_to_current_time()
        if result.sent:
            log.info("sent %d event(s)", result.sent)
        if result.error:
            if not failing:
                log.warning("sending paused: %s; retrying with backoff", result.error)
            failing = True
            pause = min(
                settings.sync_max_backoff_seconds, max(pause * 2, settings.sync_interval_seconds)
            )
        else:
            if failing:
                log.info("sending resumed")
            failing = False
            pause = settings.sync_interval_seconds
        stop.wait(pause)
    log.info("sync worker stopped")


def main() -> None:
    """Entry point of larkspur-track-sync."""
    from larkspur_track.db import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    def session_factory() -> AbstractContextManager[Session]:
        return Session(get_engine())

    run(SyncSettings(), session_factory, stop)
