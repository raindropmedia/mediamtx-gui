"""Background task that periodically pulls one frame from each online
MediaMTX stream via ffmpeg and caches it as a JPEG for the dashboard.

Design notes:
- Runs as a simple asyncio background task started on FastAPI startup,
  rather than a full scheduler dependency - one loop, one job, no need for
  APScheduler.
- Only streams currently reported "online" by MediaMTX are snapshotted,
  to avoid hammering ffmpeg against offline sources every cycle.
- Uses the dedicated "mediamtx-gui-snapshot" service credential (see
  streams_service.ensure_snapshot_credential) so it doesn't need any
  stream-specific password.
- Failures are cached per path (with a timestamp + message) so the
  dashboard can show "kein Bild verfuegbar" instead of a broken image.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import get_settings
from .crypto_utils import decrypt
from .database import SessionLocal
from .models import StreamCredential
from .mtx_client import MediaMTXClient

logger = logging.getLogger("mediamtx_gui.snapshots")

SNAPSHOT_SERVICE_USER = "mediamtx-gui-snapshot"


@dataclass
class SnapshotState:
    last_success: dict[str, dt.datetime] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)


state = SnapshotState()
_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _snapshot_credential_password() -> str | None:
    db = SessionLocal()
    try:
        cred = db.query(StreamCredential).filter_by(username=SNAPSHOT_SERVICE_USER).first()
        if not cred:
            return None
        return decrypt(cred.password_encrypted)
    finally:
        db.close()


def snapshot_path_for(name: str) -> Path:
    settings = get_settings()
    safe = "".join(c for c in name if c.isalnum() or c in "-_.")
    return Path(settings.snapshot_dir) / f"{safe}.jpg"


async def _grab_frame(path_name: str, password: str | None) -> None:
    settings = get_settings()
    creds = f"{SNAPSHOT_SERVICE_USER}:{password}@" if password else ""
    url = f"rtsp://{creds}{settings.mtx_rtsp_host}:{settings.mtx_rtsp_port}/{path_name}"
    out_path = snapshot_path_for(path_name)
    tmp_path = out_path.with_suffix(".tmp.jpg")

    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-timeout", str(settings.snapshot_timeout_seconds * 1_000_000),
        "-i", url,
        "-frames:v", "1",
        "-q:v", "4",
        "-f", "image2",
        str(tmp_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=settings.snapshot_timeout_seconds + 3)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        state.last_error[path_name] = "Zeitueberschreitung beim Abrufen des Bildes."
        return

    if proc.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
        tmp_path.replace(out_path)
        state.last_success[path_name] = dt.datetime.utcnow()
        state.last_error.pop(path_name, None)
    else:
        tmp_path.unlink(missing_ok=True)
        msg = stderr.decode("utf-8", "ignore").strip().splitlines()[-1] if stderr else "ffmpeg-Fehler"
        state.last_error[path_name] = msg[:300]
        # Bisher landete der eigentliche ffmpeg-Fehler nur im In-Memory-State
        # (fuer die Dashboard-Anzeige) und nirgends im Log - dadurch war ohne
        # Zugriff auf den Python-Prozess selbst nicht erkennbar, *warum* ein
        # Snapshot fehlschlaegt (z.B. falscher RTSP-Hostname, Auth-Fehler,
        # nicht erreichbare Quelle). Jetzt zusaetzlich als WARNING geloggt.
        logger.warning("Snapshot fuer '%s' fehlgeschlagen: %s", path_name, msg[:300])


async def _run_cycle(client: MediaMTXClient) -> None:
    try:
        data = await client.list_paths(items_per_page=1000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konnte Pfadliste fuer Snapshots nicht laden: %s", exc)
        return

    items = (data or {}).get("items", [])
    online_paths = [
        item["name"] for item in items
        if item.get("online", item.get("ready", False)) and item.get("name")
    ]
    if not online_paths:
        return

    password = _snapshot_credential_password()
    # Run a bounded number of ffmpeg pulls concurrently to avoid overloading
    # the host if there are many streams.
    semaphore = asyncio.Semaphore(4)

    async def _bounded(name: str) -> None:
        async with semaphore:
            await _grab_frame(name, password)

    await asyncio.gather(*(_bounded(name) for name in online_paths), return_exceptions=True)


async def _loop(client: MediaMTXClient) -> None:
    assert _stop_event is not None
    settings = get_settings()
    while not _stop_event.is_set():
        await _run_cycle(client)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=settings.snapshot_interval_seconds)
        except asyncio.TimeoutError:
            pass


def start_background_task(client: MediaMTXClient) -> None:
    global _task, _stop_event
    if _task is not None:
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_loop(client))
    logger.info("Snapshot-Hintergrundaufgabe gestartet.")


async def stop_background_task() -> None:
    global _task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _task is not None:
        await _task
    _task = None
    _stop_event = None
