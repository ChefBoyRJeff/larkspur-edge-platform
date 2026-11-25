"""User roles. Each role can do everything the roles below it can do."""

from enum import StrEnum


class UserRole(StrEnum):
    """Clerks handle shipments; supervisors also ship and audit; admins also manage users."""

    CLERK = "clerk"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


_RANK = {UserRole.CLERK: 1, UserRole.SUPERVISOR: 2, UserRole.ADMIN: 3}


def has_role(role: UserRole, required: UserRole) -> bool:
    """True when `role` is `required` or a role above it."""
    return _RANK[role] >= _RANK[required]
