from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..database import get_db
from ..deps import require_admin, verify_csrf
from ..models import User
from ..security import generate_password, hash_password
from ..templating import flash, render

router = APIRouter()


@router.get("/users")
def list_users(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.username).all()
    return render(request, "users.html", {"user": user, "users": users})


@router.get("/users/new")
def new_user_form(request: Request, user: User = Depends(require_admin)):
    return render(request, "user_form.html", {"user": user, "is_new": True, "target": None})


@router.post("/users/new")
async def new_user_submit(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = dict(await request.form())
    verify_csrf(request, form.get("csrf_token", ""))

    username = (form.get("username") or "").strip()
    role = form.get("role") if form.get("role") in ("admin", "user") else "user"

    if not username:
        flash(request, "Bitte einen Benutzernamen angeben.", "error")
        return RedirectResponse("/users/new", status_code=303)
    if db.query(User).filter_by(username=username).first():
        flash(request, f"Der Benutzer '{username}' existiert bereits.", "error")
        return RedirectResponse("/users/new", status_code=303)

    custom_password = (form.get("password") or "").strip()
    if custom_password and len(custom_password) < 10:
        flash(request, "Das Passwort muss mindestens 10 Zeichen lang sein. Leer lassen fuer ein automatisch generiertes Passwort.", "error")
        return RedirectResponse("/users/new", status_code=303)
    password = custom_password or generate_password(16)

    new_user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        allowed_paths=(form.get("allowed_paths") or "").strip(),
        must_change_password=True,
    )
    db.add(new_user)
    db.commit()

    log_action(db, username=user.username, action="user_created", target=username, details=f"role={role}")
    flash(
        request,
        f"Benutzer '{username}' angelegt (Rolle: {role}). "
        f"Initiales Passwort (wird nur jetzt angezeigt): {password}",
        "success",
    )
    return RedirectResponse("/users", status_code=303)


@router.get("/users/{user_id:int}/edit")
def edit_user_form(user_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        flash(request, "Benutzer nicht gefunden.", "error")
        return RedirectResponse("/users", status_code=303)
    return render(request, "user_form.html", {"user": user, "is_new": False, "target": target})


@router.post("/users/{user_id:int}/edit")
async def edit_user_submit(
    user_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        flash(request, "Benutzer nicht gefunden.", "error")
        return RedirectResponse("/users", status_code=303)

    form = dict(await request.form())
    verify_csrf(request, form.get("csrf_token", ""))

    if target.id == user.id and form.get("role") != "admin":
        flash(request, "Du kannst dir nicht selbst die Administratorrolle entziehen.", "error")
        return RedirectResponse(f"/users/{user_id}/edit", status_code=303)

    target.role = form.get("role") if form.get("role") in ("admin", "user") else target.role
    target.allowed_paths = (form.get("allowed_paths") or "").strip()
    target.active = form.get("active") == "on"

    reset_password = None
    if form.get("reset_password") == "on":
        reset_password = generate_password(16)
        target.password_hash = hash_password(reset_password)
        target.must_change_password = True

    db.commit()
    log_action(db, username=user.username, action="user_updated", target=target.username)

    msg = f"Benutzer '{target.username}' aktualisiert."
    if reset_password:
        msg += f" Neues Passwort (wird nur jetzt angezeigt): {reset_password}"
    flash(request, msg, "success")
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id:int}/delete")
async def delete_user(
    user_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        flash(request, "Benutzer nicht gefunden.", "error")
        return RedirectResponse("/users", status_code=303)
    if target.id == user.id:
        flash(request, "Du kannst dich nicht selbst loeschen.", "error")
        return RedirectResponse("/users", status_code=303)

    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    username = target.username
    db.delete(target)
    db.commit()
    log_action(db, username=user.username, action="user_deleted", target=username)
    flash(request, f"Benutzer '{username}' geloescht.", "success")
    return RedirectResponse("/users", status_code=303)
