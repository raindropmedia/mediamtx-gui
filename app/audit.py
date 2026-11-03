from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLogEntry


def log_action(db: Session, *, username: str, action: str, target: str = "", details: str = "") -> None:
    entry = AuditLogEntry(username=username, action=action, target=target, details=details)
    db.add(entry)
    db.commit()
