"""Larkspur Track: offline-first shipment tracking for a (fictional) warehouse."""


def main() -> None:
    """Run the development server on this PC only (http://127.0.0.1:8000)."""
    import uvicorn

    uvicorn.run("larkspur_track.main:app", host="127.0.0.1", port=8000, reload=True)
