"""Password rules and hashing with Argon2id.

The cost parameters are the OWASP Password Storage Cheat Sheet minimum for Argon2id (19 MiB of
memory, 2 iterations, 1 thread), not argon2-cffi's default 64 MiB profile: every sign-in hashes
once, and a web pod has a 256 MiB memory limit on a Raspberry Pi. Each stored hash records its
own parameters, so raising them later means rehashing at the next sign-in (needs_rehash).
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 128

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)


def password_problem(password: str) -> str | None:
    """Explain what is wrong with a new password, or return None when it is acceptable."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    return None


def hash_password(password: str) -> str:
    """Hash with a new random salt; the result records the algorithm and its parameters."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True when the password matches. Never raises for a wrong password or a damaged hash."""
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with different parameters than the current ones."""
    return _hasher.check_needs_rehash(password_hash)
