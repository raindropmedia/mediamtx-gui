from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_mtx_client, require_login
from ..models import StreamMeta, User
from ..mtx_client import MediaMTXClient, MediaMTXError
from ..snapshots import snapshot_path_for
from ..snapshots import state as snapshot_state
from ..templating import render

router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
    client: MediaMTXClient = Depends(get_mtx_client),
):
    error = None
    items: list[dict] = []
    try:
        runtime = await client.list_paths(items_per_page=1000)
        items = runtime.get("items", []) if runtime else []
    except MediaMTXError as exc:
        error = str(exc)

    meta_by_name = {m.path_name: m for m in db.query(StreamMeta).all()}

    streams = []
    for item in items:
        name = item.get("name")
        if not name or not user.can_see_path(name):
            continue
        meta = meta_by_name.get(name)
        readers = item.get("readers") or []
        source = item.get("source") or {}
        streams.append(
            {
                "name": name,
                "online": item.get("online", item.get("ready", False)),
                "source_type": source.get("type", "-"),
                "reader_count": len(readers),
                "display_name": meta.display_name if meta else "",
                "description": meta.description if meta else "",
                "tags": [t.strip() for t in (meta.tags if meta else "").split(",") if t.strip()],
                "has_snapshot": snapshot_path_for(name).exists(),
                "snapshot_error": snapshot_state.last_error.get(name),
                "snapshot_time": snapshot_state.last_success.get(name),
            }
        )
    streams.sort(key=lambda s: s["name"])

    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "streams": streams,
            "error": error,
            "now": dt.datetime.utcnow(),
            "stream_count": len(streams),
            "online_count": sum(1 for s in streams if s["online"]),
        },
    )


@router.get("/streams/{name:path}/snapshot.jpg")
def stream_snapshot(name: str, user: User = Depends(require_login)):
    if not user.can_see_path(name):
        return Response(status_code=403)
    path = snapshot_path_for(name)
    if path.exists():
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    return Response(status_code=404)


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
