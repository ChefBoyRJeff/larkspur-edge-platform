"""The page footer shows the running release (APP_RELEASE, set by the Helm chart)."""

import pytest
from fastapi.testclient import TestClient


def test_footer_shows_the_release(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    monkeypatch.setenv("APP_RELEASE", "sha-1a2b3c4")

    page = client.get("/shipments")

    assert "Release sha-1a2b3c4" in page.text


def test_footer_says_local_without_a_release(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    monkeypatch.delenv("APP_RELEASE", raising=False)

    page = client.get("/dashboard")

    assert "Release local" in page.text
