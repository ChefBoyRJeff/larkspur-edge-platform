"""Command-line user administration, run on the admin PC or inside a Larkspur Track pod.

    larkspur-track-admin create-user --username jdoe --display-name "Jane Doe" --role admin

The password is typed at a prompt, never passed as an argument, so it stays out of shell
history, process lists and Kubernetes manifests.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from getpass import getpass

from sqlalchemy.orm import Session

from larkspur_track.audit import AuditAction, record
from larkspur_track.db import get_engine
from larkspur_track.models import User
from larkspur_track.roles import UserRole
from larkspur_track.users import UserInputError, create_user

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


def main(argv: Sequence[str] | None = None, prompt: PasswordPrompt = getpass) -> int:
    args = build_parser().parse_args(argv)
    with Session(get_engine()) as session:
        user = run_create_user(session, args, prompt)
        if user is None:
            return 1
        session.commit()
        print(f"Created {user.role} user {user.username}.")
    return 0
