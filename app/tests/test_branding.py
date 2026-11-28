"""The page header shows the environment's name everywhere except prod."""

import pytest

from conftest import SignedInClient


@pytest.mark.parametrize(("environment", "badge"), [("dev", True), ("prod", False)])
def test_environment_badge(
    client: SignedInClient,
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
    environment: str,
    badge: bool,
) -> None:
    monkeypatch.setenv("SITE_ENVIRONMENT", environment)

    page = client.get("/shipments").text

    assert ('<span class="env">DEV</span>' in page) is badge
