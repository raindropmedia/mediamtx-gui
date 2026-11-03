from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..config import get_settings
from ..crypto_utils import decrypt
from ..database import get_db
from ..deps import get_mtx_client, require_admin, require_login, verify_csrf
from ..links import build_links
from ..models import StreamCredential, StreamMeta, User
from ..mtx_client import MediaMTXClient, MediaMTXError
from ..pathconf import PathConfFormError, build_path_conf, derive_form_defaults, validate_path_name
from ..templating import flash, render

router = APIRouter()

# NOTE on route order: FastAPI/Starlette matches routes in registration
# order, and a `{name:path}` converter greedily matches slashes too. So all
# routes with a static suffix after the name (".../edit", ".../delete", see
# also recordings.router's ".../recordings") MUST be registered before the
# generic "/streams/{name:path}" detail route below - otherwise the generic
# route would swallow "/streams/foo/edit" as name="foo/edit".


@router.get("/streams/new")
def new_stream_form(request: Request, user: User = Depends(require_admin)):
    prefill_name = request.query_params.get("name", "")
    return render(
        request,
        "stream_form.html",
        {"user": user, "is_new": True, "name": prefill_name, "defaults": derive_form_defaults(None), "raw_json": ""},
    )


@router.post("/streams/new")
async def new_stream_submit(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = dict((await request.form()))
    verify_csrf(request, form.get("csrf_token", ""))

    try:
        name = validate_path_name(form.get("name", ""))
        conf = build_path_conf(form)
    except PathConfFormError as exc:
        flash(request, str(exc), "error")
        return render(
            request,
            "stream_form.html",
            {"user": user, "is_new": True, "name": form.get("name", ""), "defaults": form, "raw_json": form.get("raw_json_overrides", "")},
            status_code=400,
        )

    existing = await client.get_path_config(name)
    if existing is not None:
        flash(request, f"Ein Stream mit dem Namen '{name}' existiert bereits.", "error")
        return RedirectResponse("/streams/new", status_code=303)

    try:
        await client.add_path_config(name, conf)
    except MediaMTXError as exc:
        flash(request, f"MediaMTX hat die Konfiguration abgelehnt: {exc.detail}", "error")
        return RedirectResponse("/streams/new", status_code=303)

    # Normalerweise gibt es fuer einen frisch bei MediaMTX angelegten Pfad noch
    # keine lokalen Metadaten - es sei denn, ein frueherer Versuch ist nach dem
    # MediaMTX-seitigen Anlegen an dieser Stelle gescheitert, oder der Pfad
    # wurde zwischenzeitlich ausserhalb der GUI aus MediaMTX entfernt (z.B.
    # durch einen Neustart, der nicht persistierte Pfad-Configs verwirft) und
    # jetzt unter demselben Namen neu angelegt. In dem Fall die vorhandene
    # Zeile aktualisieren statt mit einem UNIQUE-Constraint-Fehler abzubrechen.
    meta = db.query(StreamMeta).filter_by(path_name=name).first()
    if meta is None:
        meta = StreamMeta(path_name=name, created_by=user.username)
        db.add(meta)
    meta.display_name = (form.get("display_name") or "").strip()
    meta.description = (form.get("description") or "").strip()
    meta.tags = (form.get("tags") or "").strip()
    db.commit()
    log_action(db, username=user.username, action="stream_created", target=name, details=json.dumps(conf))
    flash(request, f"Stream '{name}' wurde angelegt.", "success")
    return RedirectResponse(f"/streams/{name}", status_code=303)


@router.get("/streams/{name:path}/edit")
async def edit_stream_form(
    name: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    conf = await client.get_path_config(name)
    if conf is None:
        flash(request, f"Stream '{name}' wurde in MediaMTX nicht gefunden.", "error")
        return RedirectResponse("/", status_code=303)

    meta = db.query(StreamMeta).filter_by(path_name=name).first()
    defaults = derive_form_defaults(conf)
    if meta:
        defaults["display_name"] = meta.display_name
        defaults["description"] = meta.description
        defaults["tags"] = meta.tags

    known_keys = {
        "source", "sourceRedirect", "sourceOnDemand", "record", "recordFormat",
        "recordSegmentDuration", "recordDeleteAfter", "maxReaders", "alwaysAvailable",
        "overridePublisher",
    }
    extra = {k: v for k, v in conf.items() if k not in known_keys}

    return render(
        request,
        "stream_form.html",
        {
            "user": user,
            "is_new": False,
            "name": name,
            "defaults": defaults,
            "raw_json": json.dumps(extra, indent=2, ensure_ascii=False) if extra else "",
        },
    )


@router.post("/streams/{name:path}/edit")
async def edit_stream_submit(
    name: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = dict((await request.form()))
    verify_csrf(request, form.get("csrf_token", ""))

    try:
        conf = build_path_conf(form)
    except PathConfFormError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(f"/streams/{name}/edit", status_code=303)

    try:
        await client.replace_path_config(name, conf)
    except MediaMTXError as exc:
        flash(request, f"MediaMTX hat die Konfiguration abgelehnt: {exc.detail}", "error")
        return RedirectResponse(f"/streams/{name}/edit", status_code=303)

    meta = db.query(StreamMeta).filter_by(path_name=name).first()
    if not meta:
        meta = StreamMeta(path_name=name, created_by=user.username)
        db.add(meta)
    meta.display_name = (form.get("display_name") or "").strip()
    meta.description = (form.get("description") or "").strip()
    meta.tags = (form.get("tags") or "").strip()
    db.commit()

    log_action(db, username=user.username, action="stream_updated", target=name, details=json.dumps(conf))
    flash(request, f"Stream '{name}' wurde aktualisiert.", "success")
    return RedirectResponse(f"/streams/{name}", status_code=303)


@router.post("/streams/{name:path}/delete")
async def delete_stream(
    name: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    try:
        await client.delete_path_config(name)
    except MediaMTXError as exc:
        flash(request, f"Loeschen fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse(f"/streams/{name}", status_code=303)

    meta = db.query(StreamMeta).filter_by(path_name=name).first()
    if meta:
        db.delete(meta)
        db.commit()

    log_action(db, username=user.username, action="stream_deleted", target=name)
    flash(request, f"Stream '{name}' wurde geloescht.", "success")
    return RedirectResponse("/", status_code=303)


@router.get("/streams/{name:path}")
async def stream_detail(
    name: str,
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    if not user.can_see_path(name):
        flash(request, "Kein Zugriff auf diesen Stream.", "error")
        return RedirectResponse("/", status_code=303)

    conf = await client.get_path_config(name)

    runtime = None
    try:
        runtime = await client.get_path(name)
    except MediaMTXError:
        pass

    is_adhoc = False
    if conf is None:
        if runtime is None:
            flash(request, f"Stream '{name}' wurde in MediaMTX nicht gefunden.", "error")
            return RedirectResponse("/", status_code=303)
        # Kein eigener PathConf-Eintrag, aber der Pfad ist gerade aktiv - das
        # passiert z.B., wenn jemand direkt (ohne die GUI) auf einen Pfad
        # published, der nur ueber eine Catch-all-Regel wie "all_others" in
        # der mediamtx.yml erfasst wird. Statt eines Fehlers zeigen wir eine
        # eingeschraenkte Ansicht mit Laufzeitdaten und einem Angebot, eine
        # eigene, dauerhafte Konfiguration fuer diesen Pfad anzulegen.
        is_adhoc = True
        conf = {}

    meta = db.query(StreamMeta).filter_by(path_name=name).first()

    global_conf = {}
    try:
        global_conf = await client.get_global_config() or {}
    except MediaMTXError:
        pass

    settings = get_settings()
    creds = [c for c in db.query(StreamCredential).all() if not c.is_system and (not c.paths_list() or name in c.paths_list())]
    example_cred = creds[0] if creds else None
    links = build_links(
        name,
        global_conf,
        settings,
        cred_user=example_cred.username if example_cred else None,
        cred_pass=decrypt(example_cred.password_encrypted) if example_cred else None,
    )

    return render(
        request,
        "stream_detail.html",
        {
            "user": user,
            "name": name,
            "conf": conf,
            "conf_pretty": json.dumps(conf, indent=2, ensure_ascii=False),
            "runtime": runtime,
            "meta": meta,
            "links": links,
            "scoped_credentials": creds,
            "is_adhoc": is_adhoc,
        },
    )
