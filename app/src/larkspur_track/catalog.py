"""Customers, carriers and warehouse locations (bins and dock doors). Functions flush but never
commit; the caller owns the transaction. Nothing is ever deleted: a record that is no longer
used is deactivated, so shipments that refer to it keep their history."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from larkspur_track.models import UNASSIGNED_CUSTOMER, Carrier, Customer, Location, LocationKind

NAME_MAX = 120
LOCATION_CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{0,19}")


class CatalogInputError(ValueError):
    """Raised with a message the person entering the data can act on."""


def clean_name(value: str) -> str:
    name = " ".join(value.split())
    if len(name) < 2:
        raise CatalogInputError("Enter a name of at least 2 characters.")
    if len(name) > NAME_MAX:
        raise CatalogInputError(f"Use {NAME_MAX} characters or fewer.")
    return name


def clean_code(value: str) -> str:
    code = value.strip().upper()
    if not LOCATION_CODE.fullmatch(code):
        raise CatalogInputError("Use 1 to 20 letters, numbers or dashes, for example A-03-2 or D5.")
    return code


def _add(session: Session, row: Customer | Carrier | Location, taken: str) -> None:
    savepoint = session.begin_nested()
    session.add(row)
    try:
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        raise CatalogInputError(taken) from None


def add_customer(session: Session, name: str) -> Customer:
    customer = Customer(name=clean_name(name), is_active=True)
    _add(session, customer, "A customer with this name already exists.")
    return customer


def add_carrier(session: Session, name: str) -> Carrier:
    carrier = Carrier(name=clean_name(name), is_active=True)
    _add(session, carrier, "A carrier with this name already exists.")
    return carrier


def add_location(session: Session, code: str, kind: LocationKind) -> Location:
    location = Location(code=clean_code(code), kind=kind, is_active=True)
    _add(session, location, "A location with this code already exists.")
    return location


def unassigned_customer(session: Session) -> Customer:
    """The customer of shipments received before customers existed (created by the migration)."""
    return session.scalars(select(Customer).where(Customer.name == UNASSIGNED_CUSTOMER)).one()


def customers(session: Session, *, active_only: bool = False) -> list[Customer]:
    statement = select(Customer).order_by(Customer.name)
    if active_only:
        statement = statement.where(Customer.is_active)
    return list(session.scalars(statement))


def carriers(session: Session, *, active_only: bool = False) -> list[Carrier]:
    statement = select(Carrier).order_by(Carrier.name)
    if active_only:
        statement = statement.where(Carrier.is_active)
    return list(session.scalars(statement))


def locations(
    session: Session, kind: LocationKind | None = None, *, active_only: bool = False
) -> list[Location]:
    statement = select(Location).order_by(Location.kind, Location.code)
    if kind is not None:
        statement = statement.where(Location.kind == kind)
    if active_only:
        statement = statement.where(Location.is_active)
    return list(session.scalars(statement))


CatalogModel = type[Customer] | type[Carrier] | type[Location]


def set_active(
    session: Session, model: CatalogModel, row_id: uuid.UUID, active: bool
) -> Customer | Carrier | Location | None:
    """Activate or deactivate a record; None when it does not exist. Unassigned stays inactive."""
    row = session.get(model, row_id)
    if row is None:
        return None
    if isinstance(row, Customer) and row.name == UNASSIGNED_CUSTOMER and active:
        raise CatalogInputError(
            "Unassigned is only for shipments received before customers existed."
        )
    row.is_active = active
    session.flush()
    return row
