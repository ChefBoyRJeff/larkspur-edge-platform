from fastapi.testclient import TestClient


def _receive(client: TestClient, tracking_number: str, consignee: str) -> str:
    response = client.post(
        "/shipments",
        data={
            "tracking_number": tracking_number,
            "consignee": consignee,
            "destination": "Dock 4, Greensboro NC",
            "note": "",
        },
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_search_finds_by_tracking_number(client: TestClient) -> None:
    _receive(client, "LRK-A-0001", "Acme Retail")
    _receive(client, "LRK-B-0002", "Blue Ridge Foods")

    page = client.get("/shipments", params={"q": "lrk-a"})

    assert page.status_code == 200
    assert "LRK-A-0001" in page.text
    assert "LRK-B-0002" not in page.text


def test_search_with_no_matches_says_so(client: TestClient) -> None:
    _receive(client, "LRK-A-0001", "Acme Retail")

    page = client.get("/shipments", params={"q": "nothing-like-this"})

    assert "No shipments match" in page.text


def test_status_filter_shows_only_that_status(client: TestClient) -> None:
    location = _receive(client, "LRK-A-0001", "Acme Retail")
    _receive(client, "LRK-B-0002", "Blue Ridge Foods")
    client.post(f"{location}/advance", data={"target": "stored"})

    page = client.get("/shipments", params={"status": "stored"})

    assert "LRK-A-0001" in page.text
    assert "LRK-B-0002" not in page.text


def test_unknown_status_filter_shows_everything(client: TestClient) -> None:
    _receive(client, "LRK-A-0001", "Acme Retail")

    page = client.get("/shipments", params={"status": "lost"})

    assert page.status_code == 200
    assert "LRK-A-0001" in page.text


def test_search_text_is_escaped(client: TestClient) -> None:
    page = client.get("/shipments", params={"q": '"><script>alert(1)</script>'})

    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text


def test_dashboard_counts_shipments_by_status(client: TestClient) -> None:
    location = _receive(client, "LRK-A-0001", "Acme Retail")
    _receive(client, "LRK-B-0002", "Blue Ridge Foods")
    client.post(f"{location}/advance", data={"target": "stored"})

    page = client.get("/dashboard")

    assert page.status_code == 200
    assert "2 shipments in total" in page.text
    assert '<span class="count">1</span><span>received</span>' in page.text
    assert '<span class="count">1</span><span>stored</span>' in page.text
    assert '<span class="count">0</span><span>picked</span>' in page.text
    assert '<span class="count">0</span><span>shipped</span>' in page.text
