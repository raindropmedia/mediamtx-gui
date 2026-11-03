from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..deps import get_playback_client, require_login
from ..models import User
from ..mtx_client import PlaybackClient
from ..templating import flash, render

router = APIRouter()


@router.get("/streams/{name:path}/recordings")
async def list_recordings(
    name: str,
    request: Request,
    user: User = Depends(require_login),
    playback: PlaybackClient = Depends(get_playback_client),
):
    if not user.can_see_path(name):
        flash(request, "Kein Zugriff auf diesen Stream.", "error")
        return RedirectResponse("/", status_code=303)

    if not playback.enabled:
        flash(
            request,
            "Die Playback-API ist nicht konfiguriert (MTX_PLAYBACK_URL leer). "
            "Aktiviere 'playback: yes' in mediamtx.yml, um Aufnahmen hier durchsuchen zu koennen.",
            "info",
        )
        return render(request, "recordings.html", {"user": user, "name": name, "recordings": [], "playback_enabled": False})

    recordings = await playback.list_recordings(name) or []
    for rec in recordings:
        start = rec.get("start", "")
        duration = rec.get("duration", "")
        rec["download_url"] = playback.download_url(name, start, str(duration)) if start else ""

    return render(
        request,
        "recordings.html",
        {"user": user, "name": name, "recordings": recordings, "playback_enabled": True},
    )
