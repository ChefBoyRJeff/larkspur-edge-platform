"""FastAPI application factory for Larkspur Track."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from larkspur_track import admin_routes, audit_routes, auth_routes
from larkspur_track.config import MetricsSettings
from larkspur_track.db import get_engine
from larkspur_track.metrics import MetricsMiddleware, start_metrics_server
from larkspur_track.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the metrics server with the app (when METRICS_PORT is set) and stop it after."""
    port = MetricsSettings().metrics_port
    server = start_metrics_server(port, lambda: Session(get_engine())) if port else None
    try:
        yield
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def create_app() -> FastAPI:
    """Build the application: health checks, sign-in, shipment pages and user administration."""
    app = FastAPI(title="Larkspur Track", version="0.1.0", lifespan=lifespan)
    app.add_middleware(MetricsMiddleware)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never touches the database."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict[str, str]:
        """Readiness: the database answers. Kubernetes routes traffic only when this passes."""
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(status_code=503, detail="database unavailable") from error
        return {"status": "ready"}

    for exception, handler in auth_routes.EXCEPTION_HANDLERS.items():
        app.add_exception_handler(exception, handler)
    app.include_router(auth_routes.router)
    app.include_router(router)
    app.include_router(admin_routes.router)
    app.include_router(audit_routes.router)
    return app


app = create_app()
