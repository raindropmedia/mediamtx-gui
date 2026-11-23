from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .deps import get_or_create_csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _fromts(value, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Unix-Zeitstempel (s) -> lokale Zeit (DISPLAY_TIMEZONE)."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    if value in (None, "", 0):
        return "-"
    try:
        tz = ZoneInfo(get_settings().display_timezone)
    except Exception:  # noqa: BLE001
        tz = dt.timezone.utc
    return dt.datetime.fromtimestamp(float(value), tz).strftime(fmt)


templates.env.filters["fromts"] = _fromts


def flash(request: Request, message: str, level: str = "info") -> None:
    bucket = request.session.get("flash", [])
    bucket.append({"level": level, "message": message})
    request.session["flash"] = bucket


def render(request: Request, template_name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    ctx["flashes"] = request.session.pop("flash", [])
    ctx["csrf_token"] = get_or_create_csrf_token(request)
    ctx.setdefault("user", None)
    ctx["studiolink_enabled"] = get_settings().studiolink_enabled
    return templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
