from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import docker_control
from ..audit import log_action
from ..credentials_sync import ensure_snapshot_credential, sync_credentials_to_mtx
from ..database import get_db
from ..deps import get_mtx_client, require_admin, verify_csrf
from ..models import AuditLogEntry, User
from ..mtx_client import MediaMTXClient, MediaMTXError
from ..templating import flash, render

router = APIRouter()

PROTOCOL_TOGGLES = [
    ("rtsp", "RTSP"),
    ("rtmp", "RTMP"),
    ("hls", "HLS"),
    ("webrtc", "WebRTC"),
    ("srt", "SRT"),
    ("playback", "Aufnahme-Wiedergabe (Playback-API)"),
]


@router.get("/server")
async def server_status(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    api_ok = await client.ping()
    global_conf = {}
    if api_ok:
        try:
            global_conf = await client.get_global_config() or {}
        except MediaMTXError:
            api_ok = False

    stats = {}
    if api_ok:
        rtsp = await client.list_rtsp_conns()
        rtmp = await client.list_rtmp_conns()
        webrtc = await client.list_webrtc_sessions()
        srt = await client.list_srt_conns()
        hls = await client.list_hls_muxers()
        stats = {
            "RTSP-Verbindungen": (rtsp or {}).get("itemCount") if rtsp else None,
            "RTMP-Verbindungen": (rtmp or {}).get("itemCount") if rtmp else None,
            "WebRTC-Sitzungen": (webrtc or {}).get("itemCount") if webrtc else None,
            "SRT-Verbindungen": (srt or {}).get("itemCount") if srt else None,
            "HLS-Muxer": (hls or {}).get("itemCount") if hls else None,
        }

    container = docker_control.get_status() if docker_control.is_configured() else None
    from ..credentials_sync import SNAPSHOT_SERVICE_USER
    from ..models import StreamCredential

    snapshot_cred = db.query(StreamCredential).filter_by(username=SNAPSHOT_SERVICE_USER).first()

    return render(
        request,
        "server.html",
        {
            "user": user,
            "api_ok": api_ok,
            "global_conf": global_conf,
            "protocol_toggles": PROTOCOL_TOGGLES,
            "stats": stats,
            "container": container,
            "docker_configured": docker_control.is_configured(),
            "snapshot_cred_exists": snapshot_cred is not None,
        },
    )


@router.post("/server/protocols/{key}/toggle")
async def toggle_protocol(
    key: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    valid_keys = {k for k, _ in PROTOCOL_TOGGLES}
    if key not in valid_keys:
        flash(request, "Unbekannter Schalter.", "error")
        return RedirectResponse("/server", status_code=303)

    try:
        current = await client.get_global_config()
        new_value = not bool(current.get(key, False))
        await client.patch_global_config({key: new_value})
    except MediaMTXError as exc:
        flash(request, f"Aenderung fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse("/server", status_code=303)

    log_action(db, username=user.username, action="protocol_toggled", target=key, details=str(new_value))
    flash(request, f"'{key}' ist jetzt {'aktiviert' if new_value else 'deaktiviert'}.", "success")
    return RedirectResponse("/server", status_code=303)


@router.get("/server/config")
async def edit_global_config_form(
    request: Request,
    user: User = Depends(require_admin),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    try:
        conf = await client.get_global_config()
    except MediaMTXError as exc:
        flash(request, f"Globale Konfiguration konnte nicht geladen werden: {exc.detail}", "error")
        return RedirectResponse("/server", status_code=303)

    return render(
        request,
        "server_config_edit.html",
        {"user": user, "conf_pretty": json.dumps(conf, indent=2, ensure_ascii=False)},
    )


@router.post("/server/config")
async def edit_global_config_submit(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    raw = (form.get("conf_json") or "").strip()

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Die Konfiguration muss ein JSON-Objekt sein.")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(request, f"Ungueltiges JSON: {exc}", "error")
        return RedirectResponse("/server/config", status_code=303)

    try:
        await client.patch_global_config(parsed)
    except MediaMTXError as exc:
        flash(request, f"MediaMTX hat die Konfiguration abgelehnt: {exc.detail}", "error")
        return RedirectResponse("/server/config", status_code=303)

    log_action(db, username=user.username, action="global_config_updated")
    flash(request, "Globale Konfiguration wurde aktualisiert.", "success")
    return RedirectResponse("/server", status_code=303)


@router.post("/server/snapshot-account/setup")
async def setup_snapshot_account(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    cred, new_password = ensure_snapshot_credential(db)
    try:
        await sync_credentials_to_mtx(client, db)
    except MediaMTXError as exc:
        flash(request, f"Dienstkonto lokal angelegt, aber Synchronisation fehlgeschlagen: {exc.detail}", "error")
        return RedirectResponse("/server", status_code=303)

    log_action(db, username=user.username, action="snapshot_account_setup")
    if new_password:
        flash(request, "Snapshot-Dienstkonto wurde eingerichtet und mit MediaMTX synchronisiert.", "success")
    else:
        flash(request, "Snapshot-Dienstkonto existiert bereits und wurde erneut synchronisiert.", "info")
    return RedirectResponse("/server", status_code=303)


@router.post("/server/docker/{action}")
async def docker_action(
    action: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    if action not in ("start", "stop", "restart"):
        flash(request, "Unbekannte Aktion.", "error")
        return RedirectResponse("/server", status_code=303)

    fn = getattr(docker_control, action)
    ok, err = fn()
    log_action(db, username=user.username, action=f"docker_{action}", details=err)
    if ok:
        flash(request, f"MediaMTX-Container: {action} ausgefuehrt.", "success")
    else:
        flash(request, f"Docker-Aktion fehlgeschlagen: {err}", "error")
    return RedirectResponse("/server", status_code=303)


@router.get("/audit-log")
def audit_log(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    entries = db.query(AuditLogEntry).order_by(AuditLogEntry.timestamp.desc()).limit(200).all()
    return render(request, "audit_log.html", {"user": user, "entries": entries})
