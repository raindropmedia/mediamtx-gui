"""Endpunkt fuer MediaMTX `authMethod: http`.

mediamtx.yml:
    authMethod: http
    authHTTPAddress: http://<gui-host>:8080/mtx/auth?secret=<MTX_AUTH_SECRET>

MediaMTX schickt pro Verbindung/API-Aufruf ein JSON (user, password, token,
ip, action, path, protocol, id, query). 200 = erlaubt, sonst abgelehnt.
"""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..config import get_settings
from ..mtx_auth import get_auth_state

router = APIRouter()
logger = logging.getLogger("mediamtx_gui.mtx_auth")

MAX_BODY = 16 * 1024


@router.post("/mtx/auth", include_in_schema=False)
async def mtx_auth(request: Request):
    s = get_settings()
    if not s.mtx_auth_secret:
        return Response(status_code=404)
    given = request.query_params.get("secret", "")
    if not hmac.compare_digest(given.encode(), s.mtx_auth_secret.encode()):
        logger.warning("/mtx/auth: falsches Secret von %s", request.client.host if request.client else "?")
        return Response(status_code=403)

    raw = await request.body()
    if len(raw) > MAX_BODY:
        return Response(status_code=413)
    try:
        req = json.loads(raw or b"{}")
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        return Response(status_code=400)

    state = get_auth_state()
    ok, reason = state.decide(req)
    state.stats.record(req, ok, reason)
    if ok:
        return Response(status_code=200)
    # Anfragen ohne Zugangsdaten sind normal (MediaMTX fragt danach erst
    # nach Benutzer/Passwort) - nur echte Fehlversuche protokollieren.
    if req.get("user") or req.get("password") or req.get("token") or "jwt=" in (req.get("query") or ""):
        logger.info(
            "MediaMTX-Zugriff abgelehnt: %s %s via %s von %s (Benutzer '%s'): %s",
            req.get("action"), req.get("path"), req.get("protocol"), req.get("ip"), (req.get("user") or "")[:64], reason,
        )
    return Response(status_code=401)
