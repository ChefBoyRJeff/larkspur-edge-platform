"""Argon2id hashing and password rules."""

import pytest

from larkspur_track.passwords import (
    MAX_LENGTH,
    MIN_LENGTH,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)

PHRASE = "correct horse battery"


def test_hash_is_argon2id_with_owasp_parameters() -> None:
    stored = hash_password(PHRASE)

    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert PHRASE not in stored


def test_right_password_verifies() -> None:
    assert verify_password(hash_password(PHRASE), PHRASE)


def test_wrong_password_fails() -> None:
    assert not verify_password(hash_password(PHRASE), PHRASE.upper())


def test_each_hash_has_its_own_salt() -> None:
    assert hash_password(PHRASE) != hash_password(PHRASE)


def test_damaged_hash_fails_without_raising() -> None:
    assert not verify_password("not-an-argon2-hash", PHRASE)


def test_new_hash_needs_no_rehash() -> None:
    assert not needs_rehash(hash_password(PHRASE))


@pytest.mark.parametrize(
    ("length", "acceptable"),
    [(MIN_LENGTH - 1, False), (MIN_LENGTH, True), (MAX_LENGTH, True), (MAX_LENGTH + 1, False)],
)
def test_password_length_rules(length: int, acceptable: bool) -> None:
    assert (password_problem("x" * length) is None) == acceptable
