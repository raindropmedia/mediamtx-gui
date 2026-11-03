"""Minimal in-memory brute-force throttle for the login form.

Deliberately simple (a dict guarded by a lock, process-local, reset on
restart) - this app is designed to run as a single container/process, so a
shared cache (Redis etc.) would be over-engineering. If this is ever scaled
to multiple replicas behind a load balancer, this protection would need to
move to a shared store.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_failed_attempts: dict[str, list[float]] = {}

WINDOW_SECONDS = 15 * 60
MAX_ATTEMPTS = 5


def _prune(key: str, now: float) -> list[float]:
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < WINDOW_SECONDS]
    _failed_attempts[key] = attempts
    return attempts


def is_locked_out(*keys: str) -> tuple[bool, int]:
    """Returns (locked, seconds_until_retry) - locked if ANY of the given
    keys (e.g. a per-username key and a per-IP key) has hit the limit."""
    now = time.time()
    with _LOCK:
        for key in keys:
            attempts = _prune(key, now)
            if len(attempts) >= MAX_ATTEMPTS:
                retry_after = int(WINDOW_SECONDS - (now - attempts[0]))
                return True, max(retry_after, 1)
    return False, 0


def record_failure(*keys: str) -> None:
    now = time.time()
    with _LOCK:
        for key in keys:
            attempts = _prune(key, now)
            attempts.append(now)
            _failed_attempts[key] = attempts


def record_success(*keys: str) -> None:
    with _LOCK:
        for key in keys:
            _failed_attempts.pop(key, None)
