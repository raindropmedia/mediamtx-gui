"""StudioLink: Reporter verwalten, QR-Codes/Links fuer die App erzeugen,
Encoder- und Geraetezugaenge ausgeben, Integrationsstatus pruefen.

Alle Schluessel und die Reporterliste liegen im StudioLink-Backend; die GUI
ruft nur dessen Admin-API auf (STUDIOLINK_API_URL / STUDIOLINK_ADMIN_TOKEN).
"""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..audit import log_action
from ..config import get_settings
from ..database import get_db
from ..deps import get_mtx_client, get_studiolink_client, require_admin, verify_csrf
from ..models import User
from ..mtx_auth import get_auth_state, permission_matches, request_refresh
from ..mtx_client import MediaMTXClient, MediaMTXError
from ..studiolink_client import StudioLinkClient, StudioLinkError
from ..templating import flash, render

router = APIRouter()

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DEVICE_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")
PROFILE_FIELDS = [
    ("width", "Breite (px)"),
    ("height", "Hoehe (px)"),
    ("fps", "Bildrate (fps)"),
    ("videoBitrateKbps", "Video-Bitrate (kbit/s)"),
    ("audioBitrateKbps", "Audio-Bitrate (kbit/s)"),
    ("uplinkLatencyMs", "SRT-Latenz Uplink (ms)"),
    ("returnLatencyMs", "SRT-Latenz Return (ms)"),
]


def _back(msg: str, request: Request, level: str = "error", to: str = "/studiolink") -> RedirectResponse:
    flash(request, msg, level)
    return RedirectResponse(to, status_code=303)


def _not_configured(request: Request, user: User):
    return render(request, "studiolink.html", {"user": user, "configured": False})


def _num(v) -> int | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _form_to_reporter(form, *, is_new: bool) -> dict:
    data: dict = {"name": (form.get("name") or "").strip()}
    if is_new:
        data["id"] = (form.get("id") or "").strip()

    proto = form.get("tsl_protocol") or ""
    if proto == "tsl31":
        data["tsl"] = {
            "protocol": "tsl31",
            "address": _num(form.get("tsl31_address")),
            "programBit": _num(form.get("tsl31_program")) or 1,
            "previewBit": _num(form.get("tsl31_preview")) or 2,
        }
    elif proto == "tsl50":
        data["tsl"] = {
            "protocol": "tsl50",
            "screen": _num(form.get("tsl50_screen")) or 0,
            "index": _num(form.get("tsl50_index")),
            "field": form.get("tsl50_field") or "lh",
        }
    else:
        data["tsl"] = None

    profile = {key: (form.get(f"profile_{key}") or "").strip() for key, _ in PROFILE_FIELDS}
    profile["codec"] = form.get("profile_codec") or ""
    data["profile"] = profile

    passphrase = (form.get("passphrase") or "").strip()
    if form.get("passphrase_clear"):
        data["passphrase"] = None
    elif passphrase:
        data["passphrase"] = passphrase
    return data


async def _path_coverage(client: MediaMTXClient, sample: str) -> bool | None:
    """Ob in MediaMTX ein Pfad (exakt, Regex oder Catch-all) den Beispielpfad
    abdeckt. None = nicht feststellbar."""
    try:
        data = await client.list_path_configs(items_per_page=1000)
    except MediaMTXError:
        return None
    for item in (data or {}).get("items", []):
        name = item.get("name") or ""
        if name in ("all", "all_others", sample):
            return True
        if name.startswith("~") and permission_matches({"action": "read", "path": name}, "read", sample):
            return True
    return False


def _checks(settings, info: dict | None, info_error: str | None, coverage: dict) -> list[dict]:
    state = get_auth_state()
    conf = state.global_conf or {}
    checks = []

    def add(ok, title, detail):
        checks.append({"ok": ok, "title": title, "detail": detail})

    add(info is not None, "StudioLink-Backend erreichbar",
        f"{settings.studiolink_api_url}" if info else (info_error or "keine Antwort"))
    if info:
        host = info["server"]["host"]
        bad_host = bool(re.match(r"^(127\.|localhost$|::1$)", host)) or "example" in host
        add(not bad_host, "SRT-Server-Adresse fuer die App",
            f"{host}:{info['server']['srtPort']}" + (" – muss der oeffentliche Name des MediaMTX-Servers sein (server.host in config.json)" if bad_host else ""))
        ws = info.get("controlWsUrl") or ""
        add(ws.startswith("wss://") and "example" not in ws, "Steuerkanal-URL (control.publicWsUrl)", ws or "nicht gesetzt – QR-Codes enthalten keinen Steuerkanal")
        kid = info["keys"]["jwtKid"]
        add(kid in state.jwks, "JWT-Schluessel in der GUI geladen",
            f"kid {kid}" + ("" if kid in state.jwks else f" fehlt (geladen: {', '.join(state.jwks) or 'keiner'}; {state.jwks_error or settings.effective_jwks_url})"))

    method = conf.get("authMethod")
    addr = conf.get("authHTTPAddress") or ""
    if state.internal_users_loaded_at is None:
        add(False, "MediaMTX-Konfiguration gelesen", state.internal_users_error or "noch nicht geladen")
    else:
        ok = method == "http" and "/mtx/auth" in addr
        add(ok, "MediaMTX nutzt diese GUI zur Anmeldung (authMethod: http)",
            f"authMethod: {method}" + (f", authHTTPAddress: {addr.split('?')[0]}" if addr else "")
            + ("" if ok else " – App-Tokens werden so nicht akzeptiert, siehe Hinweise unten"))
        add(bool(conf.get("srt")), "SRT in MediaMTX aktiv", f"srtAddress {conf.get('srtAddress', '?')}")
        api_addr = conf.get("apiAddress") or ""
        add(not api_addr.startswith(":") or method == "http", "Control-API geschuetzt",
            f"apiAddress {api_addr}" + (" (alle Schnittstellen – nur sicher, solange jeder API-Zugriff geprueft wird)" if api_addr.startswith(":") else ""))
    add(bool(settings.mtx_auth_secret), "Auth-Endpunkt /mtx/auth aktiviert", "MTX_AUTH_SECRET gesetzt" if settings.mtx_auth_secret else "MTX_AUTH_SECRET fehlt in .env")
    last = state.stats.last_request_at
    add(bool(last and time.time() - last < 600), "MediaMTX fragt den Auth-Endpunkt ab",
        f"letzte Anfrage vor {int(time.time() - last)} s, {state.stats.allowed} erlaubt / {state.stats.denied} abgelehnt" if last else "noch keine Anfrage erhalten")
    for label, ok in coverage.items():
        add(ok, f"MediaMTX-Pfad fuer {label}", "vorhanden" if ok else ("nicht pruefbar" if ok is None else "kein passender Pfad (z. B. all_others oder ~^field/…$) konfiguriert"))
    return checks


# ---------------------------------------------------------------------------
# Uebersicht
# ---------------------------------------------------------------------------

@router.get("/studiolink")
async def overview(
    request: Request,
    user: User = Depends(require_admin),
    sl: StudioLinkClient = Depends(get_studiolink_client),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    settings = get_settings()
    if not sl.enabled:
        return _not_configured(request, user)
    info = reporters = calls = devices = None
    error = None
    try:
        info = await sl.info()
        reporters = await sl.list_reporters()
        calls = (await sl.calls())[-10:][::-1]
        devices = await sl.list_devices()
    except StudioLinkError as exc:
        error = exc.detail
    coverage = {
        "Uplink (field/<id>/pgm)": await _path_coverage(client, "field/test/pgm"),
        "Return (studio/<id>/ret)": await _path_coverage(client, "studio/test/ret"),
    }
    now = time.time()
    device_summary = {
        "total": len(devices or []),
        "expiring": sum(1 for d in devices or [] if now < d["exp"] < now + 30 * 86400),
        "expired": sum(1 for d in devices or [] if d["exp"] <= now),
    }
    return render(
        request,
        "studiolink.html",
        {
            "user": user,
            "configured": True,
            "info": info,
            "reporters": reporters or [],
            "calls": calls or [],
            "error": error,
            "checks": _checks(settings, info, error, coverage),
            "denied": list(reversed(get_auth_state().stats.last_denied))[:10],
            "device_summary": device_summary,
            "auth_url_example": f"http://<gui-host>:8080/mtx/auth?secret={'<MTX_AUTH_SECRET>'}",
        },
    )


# ---------------------------------------------------------------------------
# Reporter anlegen / bearbeiten / loeschen / widerrufen
# ---------------------------------------------------------------------------

async def _form_ctx(sl: StudioLinkClient) -> dict:
    try:
        info = await sl.info()
    except StudioLinkError:
        info = {}
    return {"defaults": (info or {}).get("defaults", {}), "profile_fields": PROFILE_FIELDS}


@router.get("/studiolink/new")
async def new_form(request: Request, user: User = Depends(require_admin), sl: StudioLinkClient = Depends(get_studiolink_client)):
    if not sl.enabled:
        return _not_configured(request, user)
    return render(request, "studiolink_reporter_form.html", {"user": user, "is_new": True, "rep": None, **await _form_ctx(sl)})


@router.post("/studiolink/new")
async def new_submit(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    data = _form_to_reporter(form, is_new=True)
    if not ID_RE.match(data["id"]):
        return _back("Die ID darf nur Buchstaben, Ziffern, '-' und '_' enthalten (1-32 Zeichen).", request, to="/studiolink/new")
    try:
        rep = await sl.create_reporter(data)
    except StudioLinkError as exc:
        return _back(f"Reporter nicht angelegt: {exc.detail}", request, to="/studiolink/new")
    request_refresh()
    log_action(db, username=user.username, action="studiolink_reporter_created", target=rep["id"])
    return _back(f"Reporter '{rep['id']}' angelegt. Jetzt einen QR-Code erzeugen.", request, "success", f"/studiolink/{rep['id']}/qr")


@router.get("/studiolink/{rid}/edit")
async def edit_form(rid: str, request: Request, user: User = Depends(require_admin), sl: StudioLinkClient = Depends(get_studiolink_client)):
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        rep = await sl.get_reporter(rid)
    except StudioLinkError as exc:
        return _back(exc.detail, request)
    if rep is None:
        return _back("Reporter nicht gefunden.", request)
    return render(request, "studiolink_reporter_form.html", {"user": user, "is_new": False, "rep": rep, **await _form_ctx(sl)})


@router.post("/studiolink/{rid}/edit")
async def edit_submit(
    rid: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        await sl.update_reporter(rid, _form_to_reporter(form, is_new=False))
    except StudioLinkError as exc:
        return _back(f"Nicht gespeichert: {exc.detail}", request, to=f"/studiolink/{rid}/edit")
    log_action(db, username=user.username, action="studiolink_reporter_updated", target=rid)
    return _back(
        f"Reporter '{rid}' gespeichert. Aenderungen an Passphrase und Profil gelten erst mit einem neuen QR-Code.",
        request, "success",
    )


@router.post("/studiolink/{rid}/delete")
async def delete(
    rid: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        await sl.delete_reporter(rid)
    except StudioLinkError as exc:
        return _back(f"Nicht geloescht: {exc.detail}", request)
    request_refresh()
    log_action(db, username=user.username, action="studiolink_reporter_deleted", target=rid)
    return _back(f"Reporter '{rid}' geloescht. Seine QR-Codes sind ab sofort ungueltig.", request, "success")


@router.post("/studiolink/{rid}/revoke")
async def revoke(
    rid: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        await sl.revoke_reporter(rid)
    except StudioLinkError as exc:
        return _back(f"Widerruf fehlgeschlagen: {exc.detail}", request)
    request_refresh()
    log_action(db, username=user.username, action="studiolink_tokens_revoked", target=rid)
    return _back(
        f"Alle bisherigen QR-Codes, Links und Encoder-Zugaenge von '{rid}' sind widerrufen; die App wurde getrennt. "
        "Laufende SRT-Verbindungen bleiben bis zum naechsten Verbindungsaufbau bestehen.",
        request, "success",
    )


# ---------------------------------------------------------------------------
# QR-Code / Konfigurationslink
# ---------------------------------------------------------------------------

@router.get("/studiolink/{rid}/qr")
async def qr_form(rid: str, request: Request, user: User = Depends(require_admin), sl: StudioLinkClient = Depends(get_studiolink_client)):
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        rep = await sl.get_reporter(rid)
        info = await sl.info()
    except StudioLinkError as exc:
        return _back(exc.detail, request)
    if rep is None:
        return _back("Reporter nicht gefunden.", request)
    return render(
        request,
        "studiolink_qr.html",
        {"user": user, "rep": rep, "info": info, "result": None, "default_hours": round(info["tokenTtlSeconds"] / 3600, 2)},
    )


@router.post("/studiolink/{rid}/qr")
async def qr_issue(
    rid: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    standalone = form.get("mode") == "standalone"
    try:
        hours = float((form.get("hours") or "12").replace(",", "."))
    except ValueError:
        hours = 12.0
    if not 0 < hours <= 720:
        return _back("Gueltigkeit: mehr als 0 und hoechstens 720 Stunden.", request, to=f"/studiolink/{rid}/qr")
    try:
        rep = await sl.get_reporter(rid)
        if rep is None:
            return _back("Reporter nicht gefunden.", request)
        info = await sl.info()
        result = await sl.issue_config(rid, standalone=standalone, hours=hours)
    except StudioLinkError as exc:
        return _back(f"QR-Code nicht erzeugt: {exc.detail}", request, to=f"/studiolink/{rid}/qr")
    result["qr_data_uri"] = "data:image/svg+xml;base64," + base64.b64encode(result["qrSvg"].encode("utf-8")).decode()
    log_action(
        db, username=user.username, action="studiolink_qr_issued", target=rid,
        details=f"{'standalone' if standalone else 'verwaltet'}, {hours:g} h",
    )
    return render(
        request,
        "studiolink_qr.html",
        {"user": user, "rep": rep, "info": info, "result": result, "default_hours": hours, "standalone": standalone},
    )


# ---------------------------------------------------------------------------
# Externer Encoder vor Ort / Studiogeraete
# ---------------------------------------------------------------------------

@router.post("/studiolink/{rid}/encoder")
async def encoder_issue(
    rid: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not ID_RE.match(rid):
        return _back("Reporter nicht gefunden.", request)
    try:
        hours = float((form.get("hours") or "12").replace(",", "."))
        result = await sl.issue_encoder(rid, hours)
    except (ValueError, StudioLinkError) as exc:
        return _back(f"Encoder-Zugang nicht erzeugt: {getattr(exc, 'detail', exc)}", request, to=f"/studiolink/{rid}/qr")
    log_action(db, username=user.username, action="studiolink_encoder_issued", target=rid, details=f"{hours:g} h")
    rows = [
        ("SRT-URL (komplett)", result["srtUrl"]),
        ("Host", result["host"]),
        ("Port", str(result["port"])),
        ("Modus", "Caller"),
        ("Stream-ID", result["streamId"]),
        ("Latenz (ms)", str(result["latencyMs"])),
    ]
    return render(
        request,
        "studiolink_token.html",
        {
            "user": user,
            "title": f"Encoder-Zugang fuer {rid}",
            "intro": "Fuer einen Encoder vor Ort (z. B. ZowieBox im Encoder-Modus, Kamera mit SRT-Ausgang) im Modus "
                     "\"Externer Uplink\". Erlaubt nur das Senden auf den Uplink dieses Reporters.",
            "rows": rows,
            "exp": result["exp"],
            "back": f"/studiolink/{rid}/qr",
        },
    )


# ---------------------------------------------------------------------------
# Geraetetokens (Ultra Stream, ZowieBox, ...)
# ---------------------------------------------------------------------------

DEVICE_ID_RE = re.compile(r"^[a-f0-9]{16}$")


def _device_rows(device: dict, info: dict) -> list[tuple[str, str]]:
    token = device["token"]
    action = device["action"]
    path = device["path"]
    host = info["server"]["host"]
    srt_port = info["server"]["srtPort"]
    settings = get_settings()
    rtsp_host = settings.public_host or host
    streamid = f"{action}:{path}:jwt:{token}"
    rows = [
        ("SRT-URL", f"srt://{host}:{srt_port}?streamid={quote(streamid, safe=':/')}"),
        ("SRT Host / Port", f"{host}  /  {srt_port}  (Modus Caller)"),
        ("SRT Stream-ID", streamid),
    ]
    if action == "publish":
        rows += [
            ("RTMP-URL", f"rtmp://{rtsp_host}/{path}?jwt={token}"),
            ("RTSP-URL", f"rtsp://{rtsp_host}:8554/{path}?jwt={token}"),
        ]
    else:
        rows += [("RTSP-URL", f"rtsp://{rtsp_host}:8554/{path}?jwt={token}")]
    rows.append(("Token", token))
    return rows


@router.get("/studiolink/devices")
async def devices_list(request: Request, user: User = Depends(require_admin), sl: StudioLinkClient = Depends(get_studiolink_client)):
    if not sl.enabled:
        return _not_configured(request, user)
    devices, reporters, error = [], [], None
    try:
        devices = await sl.list_devices()
        reporters = await sl.list_reporters()
    except StudioLinkError as exc:
        error = exc.detail
    devices.sort(key=lambda d: (d.get("path", ""), d.get("name", "")))
    suggestions = []
    for r in reporters:
        suggestions += [
            (r["paths"]["return"], "publish", f"Ultra Stream: Return fuer {r['name']}"),
            (r["paths"]["uplink"], "read", f"ZowieBox: Programm von {r['name']}"),
        ]
    return render(
        request,
        "studiolink_devices.html",
        {"user": user, "devices": devices, "error": error, "suggestions": suggestions, "now": time.time()},
    )


@router.post("/studiolink/devices/new")
async def device_create(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    action = form.get("action") or ""
    path = (form.get("path") or "").strip().strip("/")
    days = _num(form.get("days")) or 365
    name = (form.get("name") or "").strip()[:80]
    if action not in ("publish", "read") or not DEVICE_PATH_RE.match(path):
        return _back("Bitte Aktion (Senden/Empfangen) und einen gueltigen Pfad angeben.", request, to="/studiolink/devices")
    try:
        device = await sl.create_device(name=name, action=action, path=path, days=days)
    except StudioLinkError as exc:
        return _back(f"Geraetetoken nicht erzeugt: {exc.detail}", request, to="/studiolink/devices")
    request_refresh()
    log_action(db, username=user.username, action="studiolink_device_created", target=path,
               details=f"{device['id']} {device['name']}, {action}, {days} Tage")
    return _back(f"Geraetetoken '{device['name']}' angelegt.", request, "success", f"/studiolink/devices/{device['id']}")


@router.get("/studiolink/devices/{device_id}")
async def device_show(
    device_id: str,
    request: Request,
    user: User = Depends(require_admin),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    if not DEVICE_ID_RE.match(device_id):
        return _back("Geraet nicht gefunden.", request, to="/studiolink/devices")
    try:
        device = await sl.get_device(device_id)
        info = await sl.info()
    except StudioLinkError as exc:
        return _back(exc.detail, request, to="/studiolink/devices")
    if device is None:
        return _back("Geraet nicht gefunden.", request, to="/studiolink/devices")
    return render(
        request,
        "studiolink_device.html",
        {"user": user, "device": device, "rows": _device_rows(device, info), "now": time.time()},
    )


@router.post("/studiolink/devices/{device_id}/delete")
async def device_delete(
    device_id: str,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    sl: StudioLinkClient = Depends(get_studiolink_client),
):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))
    if not DEVICE_ID_RE.match(device_id):
        return _back("Geraet nicht gefunden.", request, to="/studiolink/devices")
    try:
        device = await sl.get_device(device_id)
        await sl.delete_device(device_id)
    except StudioLinkError as exc:
        return _back(f"Nicht geloescht: {exc.detail}", request, to="/studiolink/devices")
    request_refresh()
    label = device["name"] if device else device_id
    log_action(db, username=user.username, action="studiolink_device_deleted", target=device["path"] if device else device_id,
               details=f"{device_id} {label}")
    return _back(
        f"Geraetetoken '{label}' geloescht. Neue Verbindungen mit diesem Token werden abgelehnt; "
        "eine laufende Verbindung bleibt bis zum naechsten Verbindungsaufbau bestehen.",
        request, "success", "/studiolink/devices",
    )
