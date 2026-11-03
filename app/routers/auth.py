from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..deps import get_current_user, verify_csrf
from ..models import User
from ..ratelimit import is_locked_out, record_failure, record_success
from ..security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from ..templating import flash, render

router = APIRouter()


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    clean_username = username.strip()
    client_ip = request.client.host if request.client else "unknown"
    rate_keys = (f"user:{clean_username.lower()}", f"ip:{client_ip}")

    locked, retry_after = is_locked_out(*rate_keys)
    if locked:
        minutes = retry_after // 60 + 1
        flash(request, f"Zu viele Fehlversuche. Bitte in etwa {minutes} Minute(n) erneut versuchen.", "error")
        log_action(db, username=clean_username or "?", action="login_blocked")
        return RedirectResponse("/login", status_code=303)

    user = db.query(User).filter_by(username=clean_username).first()
    # Always run the (slow) hash comparison, even for an unknown username,
    # against a dummy hash - so the response time doesn't reveal whether the
    # username exists.
    password_ok = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)

    if not user or not user.active or not password_ok:
        record_failure(*rate_keys)
        flash(request, "Benutzername oder Passwort ist falsch.", "error")
        log_action(db, username=clean_username or "?", action="login_failed")
        return RedirectResponse("/login", status_code=303)

    record_success(*rate_keys)
    request.session["user_id"] = user.id
    user.last_login_at = dt.datetime.utcnow()
    db.commit()
    log_action(db, username=user.username, action="login")

    if user.must_change_password:
        return RedirectResponse("/change-password", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if user_id:
        user = db.get(User, user_id)
        if user:
            log_action(db, username=user.username, action="logout")
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/change-password")
def change_password_form(request: Request, user: User | None = Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "change_password.html", {"user": user})


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_repeat: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = db.get(User, user_id)
    if not user or not verify_password(current_password, user.password_hash):
        flash(request, "Aktuelles Passwort ist falsch.", "error")
        return RedirectResponse("/change-password", status_code=303)
    if len(new_password) < 10:
        flash(request, "Das neue Passwort muss mindestens 10 Zeichen lang sein.", "error")
        return RedirectResponse("/change-password", status_code=303)
    if new_password != new_password_repeat:
        flash(request, "Die Passwoerter stimmen nicht ueberein.", "error")
        return RedirectResponse("/change-password", status_code=303)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    log_action(db, username=user.username, action="password_changed")
    flash(request, "Passwort erfolgreich geaendert.", "success")
    return RedirectResponse("/", status_code=303)
