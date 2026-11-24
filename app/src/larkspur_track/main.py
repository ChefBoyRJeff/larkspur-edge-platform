"""FastAPI application factory for Larkspur Track."""

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from larkspur_track.db import get_engine


def create_app() -> FastAPI:
    """Build the application. Routes are registered here as modules are added."""
    app = FastAPI(title="Larkspur Track", version="0.1.0")

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

    return app


app = create_app()
