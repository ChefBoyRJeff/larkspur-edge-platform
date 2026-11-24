"""FastAPI application factory for Larkspur Track."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the application. Routes are registered here as modules are added."""
    app = FastAPI(title="Larkspur Track", version="0.1.0")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness check for Kubernetes probes; never touches the database."""
        return {"status": "ok"}

    return app


app = create_app()
