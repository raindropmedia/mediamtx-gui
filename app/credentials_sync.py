"""Keeps MediaMTX's global authInternalUsers list in sync with the
StreamCredential rows managed through the GUI.

Entries in MediaMTX's authInternalUsers that do NOT correspond to a
StreamCredential row (e.g. the default "any" users, or anything an admin
added by hand outside the GUI) are left untouched - the GUI only ever
adds/updates/removes the entries it itself created, identified by username.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .crypto_utils import decrypt
from .models import ManagedCredentialUsername, StreamCredential
from .mtx_client import MediaMTXClient
from .security import generate_password

SNAPSHOT_SERVICE_USER = "mediamtx-gui-snapshot"


def _build_permissions(cred: StreamCredential) -> list[dict]:
    paths = cred.paths_list() or [""]
    perms = []
    for action in cred.actions_list():
        for path in paths:
            perms.append({"action": action, "path": path})
    return perms


def _remember_managed_usernames(db: Session, usernames: list[str]) -> None:
    """Registers usernames as GUI-managed, permanently. This registry only
    grows (rows are never removed, even after the credential itself is
    deleted) so that sync_credentials_to_mtx can still recognize and strip a
    stale entry from MediaMTX after its StreamCredential row is gone."""
    existing = {r.username for r in db.query(ManagedCredentialUsername).all()}
    changed = False
    for username in usernames:
        if username not in existing:
            db.add(ManagedCredentialUsername(username=username))
            changed = True
    if changed:
        db.commit()


async def sync_credentials_to_mtx(client: MediaMTXClient, db: Session) -> None:
    all_creds = db.query(StreamCredential).all()
    _remember_managed_usernames(db, [c.username for c in all_creds])
    managed_universe = {r.username for r in db.query(ManagedCredentialUsername).all()}

    global_conf = await client.get_global_config()
    existing = global_conf.get("authInternalUsers") or []

    kept = [entry for entry in existing if entry.get("user") not in managed_universe]

    generated = []
    for cred in all_creds:
        generated.append(
            {
                "user": cred.username,
                "pass": decrypt(cred.password_encrypted),
                "ips": cred.ips_list(),
                "permissions": _build_permissions(cred),
            }
        )

    await client.patch_global_config({"authInternalUsers": kept + generated})


def ensure_snapshot_credential(db: Session) -> tuple[StreamCredential, str | None]:
    """Ensures the internal read-only service account used for ffmpeg
    snapshot pulls exists. Returns (credential, new_plaintext_password) -
    the second element is only set when a new password was just generated
    (i.e. on first creation), so callers can log/display it once."""
    cred = db.query(StreamCredential).filter_by(username=SNAPSHOT_SERVICE_USER).first()
    if cred:
        return cred, None

    from .crypto_utils import encrypt

    plaintext = generate_password(24)
    cred = StreamCredential(
        username=SNAPSHOT_SERVICE_USER,
        password_encrypted=encrypt(plaintext),
        actions="read",
        paths="",
        ips="",
        is_system=True,
        notes="Automatisch angelegtes Dienstkonto fuer die Vorschaubild-Erzeugung (nur Lesezugriff, alle Pfade).",
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred, plaintext
