from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .deps import get_or_create_csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
    return templates.TemplateResponse(template_name, ctx, status_code=status_code)
