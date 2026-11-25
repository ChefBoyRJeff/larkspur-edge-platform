"""Prometheus metrics, served on their own port so Traefik never exposes them.

The web port (8000) stays the only one the Ingress reaches; Prometheus scrapes the metrics
port (9102 by default) from inside the cluster, which the chart's network policy allows only
for the monitoring namespace.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, start_http_server
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from larkspur_track.queries import count_by_status

REQUESTS = Counter(
    "larkspur_http_requests",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "larkspur_http_request_duration_seconds",
    "Time to handle an HTTP request, by route template.",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
SIGN_INS = Counter("larkspur_sign_ins", "Sign-in attempts, by result.", ["result"])
# Requests that matched no route (scanners, typos) share one label, so they cannot create
# an unbounded number of time series.
UNMATCHED = "unmatched"


class MetricsMiddleware:
    """Count and time every HTTP request. Labels use the route template, not the raw path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500  # unless the app sends a response, it failed

        async def send_and_note_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_note_status)
        finally:
            # The router stores the matched route in the scope.
            route = getattr(scope.get("route"), "path", None) or UNMATCHED
            method = scope["method"]
            REQUESTS.labels(method, route, str(status)).inc()
            LATENCY.labels(method, route).observe(time.perf_counter() - start)


class ShipmentCollector(Collector):
    """Shipments by status, counted from the database each time Prometheus scrapes."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self.session_factory = session_factory

    def collect(self) -> Iterator[Metric]:
        try:
            with self.session_factory() as session:
                counts = count_by_status(session)
        except SQLAlchemyError:
            return  # the scrape still succeeds; /readyz and its alert report the database
        family = GaugeMetricFamily(
            "larkspur_shipments", "Shipments in each status.", labels=["status"]
        )
        for status, count in counts.items():
            family.add_metric([status.value], count)
        yield family


def start_metrics_server(
    port: int,
    session_factory: Callable[[], AbstractContextManager[Session]],
    registry: CollectorRegistry = REGISTRY,
) -> Any:
    """Serve /metrics on the given port from a background thread; return the server."""
    registry.register(ShipmentCollector(session_factory))
    server, _thread = start_http_server(port, registry=registry)
    return server
