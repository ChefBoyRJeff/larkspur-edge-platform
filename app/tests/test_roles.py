"""Each role includes the roles below it."""

import pytest

from larkspur_track.roles import UserRole, has_role

CLERK, SUPERVISOR, ADMIN = UserRole.CLERK, UserRole.SUPERVISOR, UserRole.ADMIN


@pytest.mark.parametrize(
    ("role", "required", "allowed"),
    [
        (CLERK, CLERK, True),
        (CLERK, SUPERVISOR, False),
        (CLERK, ADMIN, False),
        (SUPERVISOR, CLERK, True),
        (SUPERVISOR, SUPERVISOR, True),
        (SUPERVISOR, ADMIN, False),
        (ADMIN, CLERK, True),
        (ADMIN, SUPERVISOR, True),
        (ADMIN, ADMIN, True),
    ],
)
def test_role_hierarchy(role: UserRole, required: UserRole, allowed: bool) -> None:
    assert has_role(role, required) is allowed
