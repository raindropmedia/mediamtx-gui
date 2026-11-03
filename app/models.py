from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


class User(Base):
    """A user of the GUI itself (not a MediaMTX stream credential)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # "admin" | "user"
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Comma-separated list of MediaMTX path names this user may see.
    # Empty string / NULL means "all paths" (default for admins and for
    # users created without a restriction).
    allowed_paths: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    def allowed_paths_list(self) -> list[str]:
        return [p.strip() for p in self.allowed_paths.split(",") if p.strip()]

    def can_see_path(self, path_name: str) -> bool:
        if self.role == "admin":
            return True
        allowed = self.allowed_paths_list()
        return not allowed or path_name in allowed


class StreamMeta(Base):
    """Extra metadata about a MediaMTX path that MediaMTX itself doesn't
    store (display name, description, tags, provenance)."""

    __tablename__ = "stream_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StreamCredential(Base):
    """A MediaMTX-level publish/read/playback credential managed by the
    GUI. Mirrored into MediaMTX's global authInternalUsers list."""

    __tablename__ = "stream_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_encrypted: Mapped[str] = mapped_column(Text, default="")
    # Comma separated actions: publish,read,playback
    actions: Mapped[str] = mapped_column(String(64), default="read")
    # Comma separated path names this credential is scoped to. Empty = all paths.
    paths: Mapped[str] = mapped_column(Text, default="")
    # Comma separated IP/CIDR allowlist. Empty = any IP.
    ips: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    def actions_list(self) -> list[str]:
        return [a.strip() for a in self.actions.split(",") if a.strip()]

    def paths_list(self) -> list[str]:
        return [p.strip() for p in self.paths.split(",") if p.strip()]

    def ips_list(self) -> list[str]:
        return [i.strip() for i in self.ips.split(",") if i.strip()]


class ManagedCredentialUsername(Base):
    """Append-only registry of every StreamCredential username the GUI has
    ever created. Used by credentials_sync to tell apart entries in
    MediaMTX's authInternalUsers that this GUI manages (and should remove
    from there once deleted here) from entries an admin added by hand
    outside the GUI (which must never be touched by the sync)."""

    __tablename__ = "managed_credential_usernames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)


class AuditLogEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[str] = mapped_column(Text, default="")
