"""Shared fixtures. Database tests use TEST_DATABASE_URL (see docs/runbooks/local-dev.md)."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from larkspur_track.auth import session_cookie_name
from larkspur_track.config import Settings, get_settings
from larkspur_track.db import get_engine, get_session
from larkspur_track.main import app
from larkspur_track.models import Carrier, Customer, Location, User
from larkspur_track.passwords import hash_password
from larkspur_track.roles import UserRole
from larkspur_track.services import StepDetails
from larkspur_track.sessions import start_session

APP_DIR = Path(__file__).resolve().parents[1]
# The password of every user that make_user creates.
PHRASE = "correct horse battery staple"
# HTTPS, as in the cluster, so the client keeps and sends Secure cookies.
BASE_URL = "https://testserver"
TEST_CUSTOMER = "Test Customer (fictional)"
TEST_CARRIER = "Test Freight (fictional)"


def _reset_database_caches() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = Settings().test_database_url
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set: run app/scripts/setup-dev-db.sh in WSL")
    return url


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    config = Config(str(APP_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(APP_DIR / "migrations"))
    return config


@pytest.fixture
def use_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Point the app at a given database URL for one test, then restore."""

    def _use(url: str) -> None:
        _reset_database_caches()
        monkeypatch.setenv("DATABASE_URL", url)

    yield _use
    _reset_database_caches()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str, alembic_config: Config) -> str:
    """The test database upgraded to the latest migration, once per test run."""
    with pytest.MonkeyPatch.context() as patch:
        _reset_database_caches()
        patch.setenv("DATABASE_URL", test_database_url)
        command.upgrade(alembic_config, "head")
        _reset_database_caches()
    return test_database_url


@pytest.fixture(scope="session")
def test_engine(migrated_database: str) -> Iterator[Engine]:
    """One engine (connection pool) for the whole run, instead of a new one per test."""
    engine = create_engine(migrated_database)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    """A session inside a transaction that is rolled back after the test, leaving no data."""
    with test_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    """Re-read settings for this test, and again afterwards so later tests see the defaults."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def step_details(db_session: Session) -> StepDetails:
    """A bin and a dock door from the migration's layout, and a test carrier."""
    bin_ = db_session.scalars(select(Location).where(Location.code == "A-01-1")).one()
    door = db_session.scalars(select(Location).where(Location.code == "D1")).one()
    carrier = db_session.scalars(select(Carrier).where(Carrier.name == TEST_CARRIER)).first()
    if carrier is None:
        carrier = Carrier(name=TEST_CARRIER, is_active=True)
        db_session.add(carrier)
        db_session.flush()
    return StepDetails(bin_id=bin_.id, dock_door_id=door.id, carrier_id=carrier.id)


@pytest.fixture
def test_customer(db_session: Session) -> Customer:
    customer = db_session.scalars(select(Customer).where(Customer.name == TEST_CUSTOMER)).first()
    if customer is None:
        customer = Customer(name=TEST_CUSTOMER, is_active=True)
        db_session.add(customer)
        db_session.flush()
    return customer


@pytest.fixture(scope="session")
def phrase_hash() -> str:
    """Argon2 is slow on purpose, so all test users share one hash of PHRASE."""
    return hash_password(PHRASE)


MakeUser = Callable[..., User]


@pytest.fixture
def make_user(db_session: Session, phrase_hash: str) -> MakeUser:
    """Create a user whose password is PHRASE."""

    def _make(
        username: str = "sam.supervisor",
        role: UserRole = UserRole.SUPERVISOR,
        *,
        is_active: bool = True,
    ) -> User:
        user = User(
            username=username,
            display_name=username.replace(".", " ").title(),
            role=role,
            password_hash=phrase_hash,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.flush()
        return user

    return _make


class SignedInClient(TestClient):
    """A test client with a session cookie. Its POSTs send the session's CSRF token.

    Unless a test passes with_defaults=False, receiving a shipment also sends the test customer,
    and advancing one sends a bin, a dock door and a carrier, so tests about other things need not
    repeat them. Tests of those fields send their own values or switch the defaults off.
    """

    csrf_token: str
    defaults: dict[str, str]

    def post(  # type: ignore[override]
        self,
        url: str,
        *,
        data: dict[str, str] | None = None,
        with_csrf: bool = True,
        with_defaults: bool = True,
        **kwargs: Any,
    ) -> httpx2.Response:
        form = dict(data or {})
        if with_csrf:
            form.setdefault("csrf_token", self.csrf_token)
        if with_defaults and url == "/shipments":
            form.setdefault("customer_id", self.defaults["customer_id"])
        if with_defaults and url.endswith("/advance"):
            for field in ("bin_id", "dock_door_id", "carrier_id"):
                form.setdefault(field, self.defaults[field])
        return super().post(url, data=form, **kwargs)


@pytest.fixture
def use_test_session(db_session: Session) -> Iterator[None]:
    """Make the app use the test session that is rolled back after each test."""
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(use_test_session: None) -> TestClient:
    """The app with nobody signed in."""
    return TestClient(app, base_url=BASE_URL, follow_redirects=False)


@pytest.fixture
def sign_in_as(
    db_session: Session,
    use_test_session: None,
    test_customer: Customer,
    step_details: StepDetails,
) -> Callable[[User], SignedInClient]:
    """A client signed in as the given user (the sign-in page itself is in test_sign_in.py)."""

    def _sign_in(user: User) -> SignedInClient:
        token, row = start_session(db_session, user, datetime.now(UTC))
        signed_in = SignedInClient(app, base_url=BASE_URL, follow_redirects=False)
        signed_in.csrf_token = row.csrf_token
        signed_in.defaults = {
            "customer_id": str(test_customer.id),
            "bin_id": str(step_details.bin_id),
            "dock_door_id": str(step_details.dock_door_id),
            "carrier_id": str(step_details.carrier_id),
        }
        signed_in.cookies.set(session_cookie_name(), token)
        return signed_in

    return _sign_in


@pytest.fixture
def client(make_user: MakeUser, sign_in_as: Callable[[User], SignedInClient]) -> SignedInClient:
    """The app with a supervisor signed in."""
    return sign_in_as(make_user())
