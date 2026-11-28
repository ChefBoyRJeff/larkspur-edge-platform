"""Sample data: users, customers, carriers and a shipment history that is not sent to Azure."""

import io
import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from larkspur_track.admin import build_parser, read_new_users, run_reset_password, run_seed
from larkspur_track.audit import AuditAction
from larkspur_track.models import AuditEvent, OutboxEvent, Shipment, ShipmentEvent
from larkspur_track.passwords import verify_password
from larkspur_track.roles import UserRole
from larkspur_track.seed import PREFIX, NewUser, SeedError, seed
from larkspur_track.users import find_user
from larkspur_track.workflow import ShipmentStatus

NOW = datetime(2026, 11, 21, 2, 0, tzinfo=UTC)  # 9 PM local, after the dock closed
PHRASE = "correct horse battery"
USERS = [
    NewUser("sam.admin", "Sam Admin", UserRole.ADMIN, PHRASE),
    NewUser("sue.lead", "Sue Lead", UserRole.SUPERVISOR, PHRASE),
    NewUser("cal.clerk", "Cal Clerk", UserRole.CLERK, PHRASE),
]


def _seed(session: Session, users=USERS, shipments: int = 40):
    return seed(
        session,
        list(users),
        days=30,
        shipments=shipments,
        now=NOW,
        zone="America/New_York",
        rng=random.Random(7),  # noqa: S311 (repeatable test data, not a secret)
    )


def test_seed_builds_a_history(db_session: Session) -> None:
    summary = _seed(db_session)

    assert summary.users == [
        ("sam.admin", "admin"),
        ("sue.lead", "supervisor"),
        ("cal.clerk", "clerk"),
    ]
    assert sum(summary.shipments.values()) == 40
    # Older shipments have finished; the newest are still in the building.
    assert summary.shipments[ShipmentStatus.SHIPPED] > 0
    assert summary.shipments[ShipmentStatus.RECEIVED] + summary.shipments[ShipmentStatus.STORED] > 0
    rows = db_session.scalars(select(Shipment).where(Shipment.tracking_number.startswith(PREFIX)))
    for shipment in rows:
        assert NOW - timedelta(days=31) <= shipment.created_at <= NOW
        times = [e.occurred_at for e in sorted(shipment.events, key=lambda e: e.occurred_at)]
        assert times[0] == shipment.created_at
        assert all(t <= NOW for t in times)
        assert shipment.customer.name != "Unassigned"


def test_seed_sends_nothing_to_azure(db_session: Session) -> None:
    _seed(db_session)

    seeded = (
        select(ShipmentEvent.id).join(Shipment).where(Shipment.tracking_number.startswith(PREFIX))
    )
    left = db_session.scalar(select(func.count()).where(OutboxEvent.event_id.in_(seeded)))
    assert left == 0


def test_seed_users_can_sign_in_and_are_audited(db_session: Session) -> None:
    _seed(db_session)

    user = find_user(db_session, "cal.clerk")
    assert user is not None and verify_password(user.password_hash, PHRASE)
    actions = list(db_session.scalars(select(AuditEvent.action)))
    assert actions.count(AuditAction.USER_CREATED.value) == 3
    assert AuditAction.SAMPLE_DATA_ADDED.value in actions


def test_seed_refuses_a_second_run(db_session: Session) -> None:
    _seed(db_session, users=USERS[:1], shipments=5)

    with pytest.raises(SeedError, match="already here"):
        _seed(db_session, users=USERS[1:], shipments=5)


def test_seed_refuses_a_weak_password(db_session: Session) -> None:
    weak = [NewUser("weak.one", "Weak One", UserRole.CLERK, "short")]

    with pytest.raises(SeedError, match="weak.one"):
        _seed(db_session, users=weak)


def test_malformed_users_json_never_echoes_it() -> None:
    with pytest.raises(SeedError) as caught:
        read_new_users(io.StringIO('[{"username": "a", "password": "Secret-Value-123"}]'))

    assert "Secret-Value-123" not in str(caught.value)


def test_seed_command_reads_users_from_stdin(db_session: Session) -> None:
    stdin = io.StringIO(
        '[{"username": "dee.clerk", "display_name": "Dee Clerk", "role": "clerk",'
        f' "password": "{PHRASE}"}}]'
    )
    args = build_parser().parse_args(["seed", "--days", "10", "--shipments", "6"])

    summary = run_seed(db_session, args, stdin)

    assert summary is not None and summary.users == [("dee.clerk", "clerk")]


def test_reset_password_command(db_session: Session, make_user) -> None:
    user = make_user("cal.reset", UserRole.CLERK)
    user.failed_sign_ins = 5
    args = build_parser().parse_args(["reset-password", "--username", user.username])
    replies = iter(["a brand new phrase", "a brand new phrase"])

    assert run_reset_password(db_session, args, lambda _p: next(replies)) is user
    assert verify_password(user.password_hash, "a brand new phrase")
    assert user.failed_sign_ins == 0


def test_reset_password_for_unknown_user(
    db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    args = build_parser().parse_args(["reset-password", "--username", "nobody.here"])

    assert run_reset_password(db_session, args, lambda _p: PHRASE) is None
    assert "no user nobody.here" in capsys.readouterr().err


def test_seed_only_works_in_opening_hours(db_session: Session) -> None:
    _seed(db_session, shipments=60)

    zone = ZoneInfo("America/New_York")
    events = db_session.scalars(
        select(ShipmentEvent).join(Shipment).where(Shipment.tracking_number.startswith(PREFIX))
    )
    for event in events:
        local = event.occurred_at.astimezone(zone)
        assert 7 <= local.hour < 17 and local.weekday() != 6, local
