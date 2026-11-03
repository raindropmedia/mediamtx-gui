from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..credentials_sync import sync_credentials_to_mtx
from ..crypto_utils import encrypt
from ..database import get_db
from ..deps import get_mtx_client, require_admin, verify_csrf
from ..models import StreamCredential, User
from ..mtx_client import MediaMTXClient, MediaMTXError
from ..security import generate_password
from ..templating import flash, render

router = APIRouter()

VALID_ACTIONS = {"publish", "read", "playback"}


@router.get("/credentials")
def list_credentials(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    creds = db.query(StreamCredential).order_by(StreamCredential.username).all()
    return render(request, "credentials.html", {"user": user, "credentials": creds})


@router.get("/credentials/new")
def new_credential_form(request: Request, user: User = Depends(require_admin)):
    return render(request, "credential_form.html", {"user": user, "is_new": True, "cred": None})


@router.post("/credentials/new")
async def new_credential_submit(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    username = (form.get("username") or "").strip()
    if not username:
        flash(request, "Bitte einen Benutzernamen angeben.", "error")
        return RedirectResponse("/credentials/new", status_code=303)
    if db.query(StreamCredential).filter_by(username=username).first():
        flash(request, f"Der Zugang '{username}' existiert bereits.", "error")
        return RedirectResponse("/credentials/new", status_code=303)

    actions = [a for a in form.getlist("actions") if a in VALID_ACTIONS]
    custom_password = (form.get("password") or "").strip()
    if custom_password and len(custom_password) < 8:
        flash(request, "Das Passwort muss mindestens 8 Zeichen lang sein. Leer lassen fuer ein automatisch generiertes Passwort.", "error")
        return RedirectResponse("/credentials/new", status_code=303)
    password = custom_password or generate_password(20)

    cred = StreamCredential(
        username=username,
        password_encrypted=encrypt(password),
        actions=",".join(actions) or "read",
        paths=(form.get("paths") or "").strip(),
        ips=(form.get("ips") or "").strip(),
        notes=(form.get("notes") or "").strip(),
    )
    db.add(cred)
    db.commit()

    try:
        await sync_credentials_to_mtx(client, db)
    except MediaMTXError as exc:
        flash(request, f"Zugang lokal gespeichert, aber Synchronisation mit MediaMTX fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse("/credentials", status_code=303)

    log_action(db, username=user.username, action="credential_created", target=username)
    flash(
        request,
        f"Zugang '{username}' angelegt. Passwort (wird nur jetzt angezeigt): {password}",
        "success",
    )
    return RedirectResponse("/credentials", status_code=303)


@router.get("/credentials/{cred_id:int}/edit")
def edit_credential_form(cred_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    cred = db.get(StreamCredential, cred_id)
    if not cred:
        flash(request, "Zugang nicht gefunden.", "error")
        return RedirectResponse("/credentials", status_code=303)
    return render(request, "credential_form.html", {"user": user, "is_new": False, "cred": cred})


@router.post("/credentials/{cred_id:int}/edit")
async def edit_credential_submit(
    cred_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    cred = db.get(StreamCredential, cred_id)
    if not cred:
        flash(request, "Zugang nicht gefunden.", "error")
        return RedirectResponse("/credentials", status_code=303)

    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    actions = [a for a in form.getlist("actions") if a in VALID_ACTIONS]
    cred.actions = ",".join(actions) or "read"
    cred.paths = (form.get("paths") or "").strip()
    cred.ips = (form.get("ips") or "").strip()
    cred.notes = (form.get("notes") or "").strip()

    new_password = (form.get("password") or "").strip()
    if new_password and len(new_password) < 8:
        flash(request, "Das Passwort muss mindestens 8 Zeichen lang sein.", "error")
        return RedirectResponse(f"/credentials/{cred_id}/edit", status_code=303)
    shown_password = None
    if new_password:
        cred.password_encrypted = encrypt(new_password)
        shown_password = new_password

    db.commit()

    try:
        await sync_credentials_to_mtx(client, db)
    except MediaMTXError as exc:
        flash(request, f"Aenderung lokal gespeichert, aber Synchronisation mit MediaMTX fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse("/credentials", status_code=303)

    log_action(db, username=user.username, action="credential_updated", target=cred.username)
    msg = f"Zugang '{cred.username}' aktualisiert."
    if shown_password:
        msg += f" Neues Passwort (wird nur jetzt angezeigt): {shown_password}"
    flash(request, msg, "success")
    return RedirectResponse("/credentials", status_code=303)


@router.post("/credentials/{cred_id:int}/delete")
async def delete_credential(
    cred_id: int,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    cred = db.get(StreamCredential, cred_id)
    if not cred:
        flash(request, "Zugang nicht gefunden.", "error")
        return RedirectResponse("/credentials", status_code=303)
    if cred.is_system:
        flash(request, "Systeminterne Zugaenge koennen nicht geloescht werden.", "error")
        return RedirectResponse("/credentials", status_code=303)

    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    username = cred.username
    db.delete(cred)
    db.commit()

    try:
        await sync_credentials_to_mtx(client, db)
    except MediaMTXError as exc:
        flash(request, f"Lokal geloescht, aber Synchronisation mit MediaMTX fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse("/credentials", status_code=303)

    log_action(db, username=user.username, action="credential_deleted", target=username)
    flash(request, f"Zugang '{username}' geloescht.", "success")
    return RedirectResponse("/credentials", status_code=303)
