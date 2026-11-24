import pytest

from larkspur_track.forms import clean_note, parse_shipment_form


def test_valid_form_is_cleaned() -> None:
    data, errors = parse_shipment_form(" lrk-test-0001 ", "  Acme   Retail ", " Dock 4 ", "  ")

    assert errors == {}
    assert data is not None
    assert (data.tracking_number, data.consignee, data.destination, data.note) == (
        "LRK-TEST-0001",
        "Acme Retail",
        "Dock 4",
        None,
    )


@pytest.mark.parametrize("tracking", ["", "abc", "LRK TEST", "LRK_TEST_0001", "X" * 41])
def test_bad_tracking_numbers_are_rejected(tracking: str) -> None:
    data, errors = parse_shipment_form(tracking, "Acme", "Dock 4", "")

    assert data is None
    assert "tracking_number" in errors


def test_required_fields_and_length_limits() -> None:
    data, errors = parse_shipment_form("LRK-TEST-0001", "   ", "x" * 201, "y" * 501)

    assert data is None
    assert set(errors) == {"consignee", "destination", "note"}


def test_clean_note() -> None:
    assert clean_note("   ") == (None, None)
    assert clean_note(" Bay 12 ") == ("Bay 12", None)
    note, error = clean_note("z" * 501)
    assert note is None
    assert error is not None
