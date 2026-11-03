"""Symmetric encryption for secrets stored at rest (stream credential
passwords, docker/API secrets a user enters through the UI, etc.).

We deliberately do NOT hash these values, because the plaintext must be
recoverable to keep MediaMTX's authInternalUsers config in sync and to show
ready-to-use connection URLs to admins. Confidentiality at rest is instead
provided by symmetric (Fernet) encryption using a key that only exists in
the deployment's environment (.env), never in the database itself.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY ist nicht gesetzt. Bitte in der .env-Datei "
            "konfigurieren (siehe .env.example)."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    if value is None:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
