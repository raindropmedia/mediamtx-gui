from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import User
from .mtx_client import MediaMTXClient, PlaybackClient

_mtx_client: MediaMTXClient | None = None
_playback_client: PlaybackClient | None = None


def get_mtx_client() -> MediaMTXClient:
    global _mtx_client
    if _mtx_client is None:
        s = get_settings()
        _mtx_client = MediaMTXClient(s.mtx_api_url, s.mtx_api_user, s.mtx_api_pass)
    return _mtx_client


def get_playback_client() -> PlaybackClient:
    global _playback_client
    if _playback_client is None:
        s = get_settings()
        _playback_client = PlaybackClient(s.mtx_playback_url)
    return _playback_client


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.active:
        return None
    return user


def require_login(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur fuer Administratoren.")
    return user


def get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted_token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not submitted_token or not secrets.compare_digest(expected, submitted_token):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungueltiges CSRF-Token. Bitte Seite neu laden.")
