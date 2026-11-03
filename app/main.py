from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import SessionLocal, init_db
from .deps import get_mtx_client, get_studiolink_client
from .mtx_auth import start_auth_task, stop_auth_task
from .models import User
from .security import generate_password, hash_password
from .snapshots import start_background_task, stop_background_task
from .templating import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mediamtx_gui")


class _HideAuthEndpoint(logging.Filter):
    """MediaMTX ruft /mtx/auth bei jeder Verbindung auf; die URL enthaelt das
    Secret. Diese Zeilen nicht ins Zugriffsprotokoll schreiben."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/mtx/auth" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_HideAuthEndpoint())

settings = get_settings()

app = FastAPI(title="MediaMTX GUI")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age_minutes * 60,
    same_site="lax",
    https_only=settings.force_secure_cookies,
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Baseline hardening headers for a self-hosted admin tool.

    Notably: a strict script-src (no inline scripts anywhere in the
    templates - the one bit of theme-init JS lives in static/js/theme-init.js
    instead) and a global no-store so pages that show a one-time plaintext
    password (new user/credential, password reset) aren't cached by the
    browser or an intermediate proxy.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    if settings.force_secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _seed_default_admin() -> None:
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        password = generate_password(16)
        admin = User(
            username="admin",
            password_hash=hash_password(password),
            role="admin",
            must_change_password=True,
        )
        db.add(admin)
        db.commit()
        logger.info("=" * 70)
        logger.info("Erstes Admin-Konto angelegt.")
        logger.info("  Benutzername: admin")
        logger.info("  Passwort:     %s", password)
        logger.info("  Bitte nach dem ersten Login sofort aendern!")
        logger.info("=" * 70)
    finally:
        db.close()


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    _seed_default_admin()
    client = get_mtx_client()
    start_background_task(client)
    start_auth_task(client, get_studiolink_client())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_background_task()
    await stop_auth_task()
    client = get_mtx_client()
    await client.aclose()
    await get_studiolink_client().aclose()
    from .deps import get_playback_client

    await get_playback_client().aclose()


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    if exc.status_code == 403:
        return render(request, "error.html", {"message": exc.detail or "Zugriff verweigert."}, status_code=403)
    if exc.status_code == 404:
        return render(request, "error.html", {"message": "Seite nicht gefunden."}, status_code=404)
    return await default_http_exception_handler(request, exc)


from .routers import auth, credentials, dashboard, mtx_auth, recordings, server, streams, studiolink, users  # noqa: E402

# Route order matters: dashboard's "/streams/{name}/snapshot.jpg" and
# recordings' "/streams/{name}/recordings" have a static suffix after the
# path name and must be registered before streams.router's catch-all
# "/streams/{name:path}" detail route, or that catch-all would swallow them.
app.include_router(dashboard.router)
app.include_router(auth.router)
app.include_router(recordings.router)
app.include_router(streams.router)
app.include_router(credentials.router)
app.include_router(users.router)
app.include_router(server.router)
app.include_router(studiolink.router)
app.include_router(mtx_auth.router)
