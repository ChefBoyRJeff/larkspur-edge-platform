"""Validation for shipment forms. Pure functions: no database and no web framework."""

import re
from dataclasses import dataclass

TRACKING_NUMBER = re.compile(r"[A-Z0-9-]{4,40}")
NOTE_MAX = 500


@dataclass(frozen=True)
class ShipmentInput:
    """Cleaned, valid values for a new shipment."""

    tracking_number: str
    consignee: str
    destination: str
    note: str | None


def clean_note(note: str) -> tuple[str | None, str | None]:
    """Return (cleaned note or None, error message or None)."""
    cleaned = note.strip()
    if len(cleaned) > NOTE_MAX:
        return None, f"Use {NOTE_MAX} characters or fewer."
    return cleaned or None, None


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def parse_shipment_form(
    tracking_number: str, consignee: str, destination: str, note: str
) -> tuple[ShipmentInput | None, dict[str, str]]:
    """Clean the form; return (input, {}) when valid or (None, field errors) when not."""
    tracking = tracking_number.strip().upper()
    consignee_clean = _clean_text(consignee)
    destination_clean = _clean_text(destination)
    note_clean, note_error = clean_note(note)

    errors: dict[str, str] = {}
    if not TRACKING_NUMBER.fullmatch(tracking):
        errors["tracking_number"] = "Use 4 to 40 letters, numbers or dashes."
    if not consignee_clean:
        errors["consignee"] = "Enter who the shipment is for."
    elif len(consignee_clean) > 120:
        errors["consignee"] = "Use 120 characters or fewer."
    if not destination_clean:
        errors["destination"] = "Enter where the shipment is going."
    elif len(destination_clean) > 200:
        errors["destination"] = "Use 200 characters or fewer."
    if note_error:
        errors["note"] = note_error

    if errors:
        return None, errors
    return ShipmentInput(tracking, consignee_clean, destination_clean, note_clean), {}
