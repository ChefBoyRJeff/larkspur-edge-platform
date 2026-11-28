"""Command-line user administration, run on the admin PC or inside a Larkspur Track pod.

    larkspur-track-admin create-user --username jdoe --display-name "Jane Doe" --role admin
    larkspur-track-admin reset-password --username jdoe
    larkspur-track-admin seed --days 60 --shipments 400 < users.json

Passwords are typed at a prompt or read from standard input, never passed as arguments, so they
stay out of shell history, process lists and Kubernetes manifests.
"""

import argparse
import json
import random
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from getpass import getpass
from typing import TextIO

from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record
from larkspur_track.config import get_settings
from larkspur_track.db import get_engine
from larkspur_track.models import User
from larkspur_track.roles import UserRole
from larkspur_track.seed import NewUser, SeedError, SeedSummary, seed
from larkspur_track.users import UserInputError, create_user, find_user, reset_password

PasswordPrompt = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="larkspur-track-admin", description="Manage Larkspur Track users."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-user", help="Create a user. The password is asked for twice."
    )
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", required=True, choices=[role.value for role in UserRole])
    reset = commands.add_parser(
        "reset-password",
        help="Set a new password, clear a lockout and sign the user out. Asked for twice.",
    )
    reset.add_argument("--username", required=True)
    sample = commands.add_parser(
        "seed",
        help="Add sample users, customers, carriers and shipments. Users come as JSON on stdin.",
    )
    sample.add_argument("--days", type=int, default=60, choices=range(1, 366), metavar="1-365")
    sample.add_argument(
        "--shipments", type=int, default=400, choices=range(1, 5001), metavar="1-5000"
    )
    return parser


def run_create_user(
    session: Session, args: argparse.Namespace, prompt: PasswordPrompt
) -> User | None:
    """Ask for the password twice and create the user; print why and return None on failure."""
    password = prompt("Password: ")
    if prompt("Repeat the password: ") != password:
        print("The passwords do not match. No user was created.", file=sys.stderr)
        return None
    try:
        user = create_user(
            session,
            username=args.username,
            display_name=args.display_name,
            role=UserRole(args.role),
            password=password,
        )
    except UserInputError as error:
        print(f"{error} No user was created.", file=sys.stderr)
        return None
    detail = f"{user.username} as {user.role} (larkspur-track-admin)"
    record(session, AuditAction.USER_CREATED, target_type="user", target_id=user.id, detail=detail)
    return user


def run_reset_password(
    session: Session, args: argparse.Namespace, prompt: PasswordPrompt
) -> User | None:
    """Ask for the new password twice and set it; print why and return None on failure."""
    user = find_user(session, args.username)
    if user is None:
        print(f"There is no user {args.username}. Nothing was changed.", file=sys.stderr)
        return None
    password = prompt("New password: ")
    if prompt("Repeat the new password: ") != password:
        print("The passwords do not match. Nothing was changed.", file=sys.stderr)
        return None
    try:
        reset_password(session, user, password, datetime.now(UTC))
    except UserInputError as error:
        print(f"{error} Nothing was changed.", file=sys.stderr)
        return None
    detail = f"{user.username} (larkspur-track-admin)"
    record(
        session,
        AuditAction.USER_PASSWORD_RESET,
        target_type="user",
        target_id=user.id,
        detail=detail,
    )
    return user


def read_new_users(stream: TextIO) -> list[NewUser]:
    """Parse [{"username", "display_name", "role", "password"}, ...]; SeedError if malformed."""
    try:
        rows = json.load(stream)
        return [
            NewUser(
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                role=UserRole(row["role"]),
                password=str(row["password"]),
            )
            for row in rows
        ]
    except ValueError, TypeError, KeyError:
        # The error text could quote a password, so it is not repeated.
        raise SeedError(
            "The users on standard input are not valid JSON in the expected shape."
        ) from None


def run_seed(session: Session, args: argparse.Namespace, stream: TextIO) -> SeedSummary | None:
    try:
        return seed(
            session,
            read_new_users(stream),
            days=args.days,
            shipments=args.shipments,
            now=datetime.now(UTC),
            zone=get_settings().display_timezone,
            rng=random.SystemRandom(),
        )
    except SeedError as error:
        print(error, file=sys.stderr)
        return None


def _print_summary(summary: SeedSummary) -> None:
    for username, role in summary.users:
        print(f"Created {role} user {username}.")
    print(f"Customers: {summary.customers}. Carriers: {summary.carriers}.")
    print("Shipments: " + ", ".join(f"{k} {v}" for k, v in summary.shipments.items()) + ".")


def main(
    argv: Sequence[str] | None = None,
    prompt: PasswordPrompt = getpass,
    stream: TextIO = sys.stdin,
) -> int:
    args = build_parser().parse_args(argv)
    with Session(get_engine()) as session:
        if args.command == "seed":
            summary = run_seed(session, args, stream)
            if summary is None:
                return 1
            session.commit()
            _print_summary(summary)
            return 0
        if args.command == "reset-password":
            user = run_reset_password(session, args, prompt)
            if user is None:
                return 1
            session.commit()
            print(f"Reset the password for {user.username}. Their sessions were ended.")
            return 0
        user = run_create_user(session, args, prompt)
        if user is None:
            return 1
        session.commit()
        print(f"Created {user.role} user {user.username}.")
    return 0
