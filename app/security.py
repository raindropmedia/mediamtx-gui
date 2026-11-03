from __future__ import annotations

import secrets

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# A bcrypt hash of a random value nothing will ever match. Used to make a
# login attempt against a username that doesn't exist take the same time as
# one against a real username with a wrong password - otherwise the
# short-circuit ("no such user" -> skip the expensive hash check) leaks
# which usernames exist via a timing side channel.
DUMMY_PASSWORD_HASH = pwd_context.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def generate_password(length: int = 16) -> str:
    """A random, URL-safe password suitable for stream credentials or the
    initial admin account."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
