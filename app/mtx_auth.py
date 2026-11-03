"""MediaMTX HTTP authentication (authMethod: http) for GUI credentials AND
StudioLink tokens.

Why: MediaMTX supports exactly one authMethod. "internal" knows only the
authInternalUsers list (managed by this GUI), "jwt" knows only tokens. The
StudioLink app and studio devices authenticate with ES256 JWTs signed by the
StudioLink backend, while existing encoders/players use the usernames and
passwords managed under "Zugangsdaten". With `authMethod: http` MediaMTX asks
this endpoint for every connection, and we accept either:

1. an entry of MediaMTX's own authInternalUsers list, evaluated with the same
   rules as MediaMTX's internal method (so the existing "any"/localhost
   entries and all GUI-managed credentials keep working unchanged), or
2. a valid StudioLink JWT (signature via JWKS, exp, permission for
   action/path, not revoked for that reporter), or
3. for action=api/metrics/pprof: the GUI's own MTX_API_USER/MTX_API_PASS
   (checked directly, so the GUI can always reach the API it needs to load
   the user list - no chicken-and-egg problem).

Everything needed for a decision is held in memory and refreshed in the
background, so a decision never waits for the StudioLink backend. The last
good JWKS is also written to disk so tokens keep working after a GUI restart
while the backend is down.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from .config import get_settings
from .mtx_client import MediaMTXClient, MediaMTXError
from .studiolink_client import StudioLinkClient, StudioLinkError

logger = logging.getLogger("mediamtx_gui.mtx_auth")

ADMIN_ACTIONS = {"api", "metrics", "pprof"}
REPORTER_PATH = re.compile(r"^(?:field/([A-Za-z0-9_-]+)/pgm|studio/([A-Za-z0-9_-]+)/ret)$")
JWKS_REFRESH_SECONDS = 300
JWKS_MISS_COOLDOWN = 30


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# Permission matching (same semantics as MediaMTX)
# ---------------------------------------------------------------------------

def permission_matches(perm: dict, action: str, path: str) -> bool:
    if perm.get("action") != action:
        return False
    if action in ADMIN_ACTIONS:
        return True
    ppath = perm.get("path") or ""
    if ppath == "":
        return True
    if ppath.startswith("~"):
        try:
            return re.search(ppath[1:], path) is not None
        except re.error:
            return False
    return ppath == path


def _ip_allowed(ips: list[str] | None, ip: str) -> bool:
    if not ips:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in ips:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _password_matches(stored: str | None, given: str) -> bool | None:
    """True/False, or None if the stored format is not supported here."""
    stored = stored or ""
    if stored.startswith("sha256:"):
        digest = base64.b64encode(hashlib.sha256(given.encode("utf-8")).digest()).decode()
        return _consteq(stored[len("sha256:"):], digest)
    if stored.startswith("argon2:"):
        return None
    return _consteq(stored, given)


def internal_users_allow(users: list[dict], *, user: str, password: str, ip: str, action: str, path: str) -> bool:
    """Evaluates MediaMTX authInternalUsers like MediaMTX does."""
    for entry in users:
        euser = entry.get("user") or ""
        if euser != "any":
            if not user or not _consteq(euser, user):
                continue
            ok = _password_matches(entry.get("pass"), password)
            if ok is None:
                logger.warning("authInternalUsers: Passwortformat von '%s' (argon2) wird nicht unterstuetzt.", euser)
                continue
            if not ok:
                continue
        if not _ip_allowed(entry.get("ips"), ip):
            continue
        if any(permission_matches(p, action, path) for p in entry.get("permissions") or []):
            return True
    return False


# ---------------------------------------------------------------------------
# JWT (ES256)
# ---------------------------------------------------------------------------

class TokenError(Exception):
    pass


def _jwk_to_key(jwk: dict) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise TokenError("JWKS: nur EC P-256 wird unterstuetzt")
    x = int.from_bytes(_b64d(jwk["x"]), "big")
    y = int.from_bytes(_b64d(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def verify_es256(token: str, keys: dict[str, ec.EllipticCurvePublicKey], now: float | None = None) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("kein JWT")
    try:
        header = json.loads(_b64d(parts[0]))
        claims = json.loads(_b64d(parts[1]))
        sig = _b64d(parts[2])
    except Exception as exc:  # noqa: BLE001
        raise TokenError("JWT nicht lesbar") from exc
    if header.get("alg") != "ES256":
        raise TokenError("Algorithmus nicht erlaubt")
    key = keys.get(header.get("kid") or "")
    if key is None:
        if header.get("kid") is None and len(keys) == 1:
            key = next(iter(keys.values()))
        else:
            raise TokenError(f"unbekannte kid {header.get('kid')}")
    if len(sig) != 64:
        raise TokenError("Signatur hat falsche Laenge")
    der = utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        key.verify(der, f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise TokenError("Signatur ungueltig") from exc
    now = time.time() if now is None else now
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now:
        raise TokenError("abgelaufen")
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + 60:
        raise TokenError("noch nicht gueltig")
    return claims


def looks_like_jwt(value: str) -> bool:
    return value.startswith("eyJ") and value.count(".") == 2


def extract_token(req: dict) -> str:
    """MediaMTX passes JWTs in different places depending on protocol/version:
    the `token` field (Bearer), the SRT streamid / RTSP password (user "jwt"),
    or a `jwt=` query parameter."""
    token = req.get("token") or ""
    if token:
        return token
    password = req.get("password") or ""
    if looks_like_jwt(password) and (req.get("user") or "") in ("", "jwt"):
        return password
    query = req.get("query") or ""
    if query:
        vals = parse_qs(query.lstrip("?")).get("jwt")
        if vals and looks_like_jwt(vals[0]):
            return vals[0]
    return ""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class AuthStats:
    allowed: int = 0
    denied: int = 0
    last_request_at: float | None = None
    last_denied: list[dict] = field(default_factory=list)

    def record(self, req: dict, ok: bool, reason: str) -> None:
        self.last_request_at = time.time()
        if ok:
            self.allowed += 1
            return
        self.denied += 1
        self.last_denied.append(
            {
                "at": self.last_request_at,
                "user": (req.get("user") or "")[:64],
                "ip": req.get("ip") or "",
                "action": req.get("action") or "",
                "path": req.get("path") or "",
                "protocol": req.get("protocol") or "",
                "reason": reason,
            }
        )
        del self.last_denied[:-20]


class AuthState:
    def __init__(self) -> None:
        self.internal_users: list[dict] = []
        # Zugaenge aus der GUI-Datenbank ("Zugangsdaten"), direkt ausgewertet.
        # Grund: Per Control-API in authInternalUsers geschriebene Eintraege
        # speichert MediaMTX nicht in mediamtx.yml - nach einem Neustart von
        # MediaMTX waeren sie sonst verschwunden.
        self.gui_credentials: list[dict] = []
        self.internal_users_loaded_at: float | None = None
        self.internal_users_error: str | None = None
        self._last_users_attempt = 0.0
        self.jwks: dict[str, ec.EllipticCurvePublicKey] = {}
        self.jwks_loaded_at: float | None = None
        self.jwks_source: str = ""
        self.jwks_error: str | None = None
        self._jwks_wanted = False
        self._last_miss_refresh = 0.0
        self.reporters: dict[str, dict] | None = None
        self.reporters_loaded_at: float | None = None
        self.reporters_error: str | None = None
        # Gueltige Geraete-IDs (jti). None = Liste (noch) nicht geladen.
        self.devices: set[str] | None = None
        self.devices_loaded_at: float = 0.0
        # Alle IDs, die seit dem Start der GUI je in der Liste standen.
        self.devices_seen: set[str] = set()
        self.global_conf: dict = {}
        self.stats = AuthStats()

    # ---- loading ----------------------------------------------------------
    def _set_jwks(self, data: dict, source: str) -> None:
        keys = {}
        for jwk in data.get("keys", []):
            try:
                keys[jwk.get("kid") or ""] = _jwk_to_key(jwk)
            except Exception as exc:  # noqa: BLE001
                logger.warning("JWKS-Schluessel uebersprungen: %s", exc)
        if not keys:
            raise TokenError("JWKS enthaelt keinen verwendbaren Schluessel")
        self.jwks = keys
        self.jwks_loaded_at = time.time()
        self.jwks_source = source
        self.jwks_error = None

    def load_cached_jwks(self) -> None:
        path = Path(get_settings().jwks_cache_path)
        if path.exists() and not self.jwks:
            try:
                self._set_jwks(json.loads(path.read_text()), "Cache-Datei")
            except Exception as exc:  # noqa: BLE001
                logger.warning("JWKS-Cache nicht lesbar: %s", exc)

    async def refresh_jwks(self, http: httpx.AsyncClient) -> None:
        url = get_settings().effective_jwks_url
        if not url:
            return
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            data = resp.json()
            self._set_jwks(data, url)
            path = Path(get_settings().jwks_cache_path)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            self.jwks_error = str(exc)
            logger.warning("JWKS von %s nicht ladbar: %s", url, exc)

    async def refresh_internal_users(self, client: MediaMTXClient) -> None:
        self._last_users_attempt = time.time()
        try:
            conf = await client.get_global_config()
            self.global_conf = conf or {}
            self.internal_users = list(self.global_conf.get("authInternalUsers") or [])
            self.internal_users_loaded_at = time.time()
            self.internal_users_error = None
        except MediaMTXError as exc:
            self.internal_users_error = exc.detail
            logger.warning("authInternalUsers nicht ladbar: %s", exc.detail)

    def _load_gui_credentials(self) -> list[dict]:
        from .credentials_sync import _build_permissions
        from .crypto_utils import decrypt
        from .database import SessionLocal
        from .models import StreamCredential

        db = SessionLocal()
        try:
            out = []
            for cred in db.query(StreamCredential).all():
                password = decrypt(cred.password_encrypted)
                if not password:
                    continue
                out.append({"user": cred.username, "pass": password, "ips": cred.ips_list(), "permissions": _build_permissions(cred)})
            return out
        finally:
            db.close()

    async def refresh_gui_credentials(self) -> None:
        try:
            self.gui_credentials = await asyncio.to_thread(self._load_gui_credentials)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GUI-Zugangsdaten nicht ladbar: %s", exc)

    async def refresh_reporters(self, sl: StudioLinkClient) -> None:
        if not sl.enabled:
            return
        try:
            items = await sl.list_reporters()
            self.reporters = {r["id"]: r for r in items}
            self.reporters_loaded_at = time.time()
            self.reporters_error = None
            started = time.time()
            self.devices = {d["id"] for d in await sl.list_devices()}
            self.devices_seen |= self.devices
            self.devices_loaded_at = started
        except StudioLinkError as exc:
            self.reporters_error = exc.detail

    # ---- decision ---------------------------------------------------------
    def _jwt_allows(self, token: str, action: str, path: str) -> tuple[bool, str]:
        if not self.jwks:
            return False, "kein JWKS geladen"
        try:
            claims = verify_es256(token, self.jwks)
        except TokenError as exc:
            if "unbekannte kid" in str(exc) and time.time() - self._last_miss_refresh > JWKS_MISS_COOLDOWN:
                # Schluessel evtl. neu erzeugt: JWKS sofort neu laden (gedrosselt).
                self._last_miss_refresh = time.time()
                self._jwks_wanted = True
                request_refresh()
            return False, f"JWT: {exc}"
        perms = claims.get(get_settings().mtx_jwt_claim_key) or []
        if not any(isinstance(p, dict) and permission_matches(p, action, path) for p in perms):
            return False, "JWT: keine Berechtigung fuer Aktion/Pfad"
        if claims.get("sub") == "device":
            jti = claims.get("jti")
            # Tokens ohne jti stammen aus aelteren Versionen und bleiben bis zum Ablauf gueltig.
            if jti and self.devices is not None and jti not in self.devices:
                # Geloescht, wenn es schon einmal in der Liste stand oder vor dem
                # letzten Abruf ausgestellt wurde (+1 s, weil iat sekundengenau ist).
                # Sonst ist es neu und nur noch nicht geladen.
                if jti in self.devices_seen or (claims.get("iat") or 0) + 1 < self.devices_loaded_at:
                    return False, f"Geraetetoken {jti} geloescht"
                request_refresh()
            return True, "Geraetetoken"
        m = REPORTER_PATH.match(path)
        if m and self.reporters is not None:
            rid = m.group(1) or m.group(2)
            rep = self.reporters.get(rid)
            if rep is None:
                return False, f"JWT: Reporter {rid} existiert nicht mehr"
            if (claims.get("iat") or 0) < (rep.get("revokedBefore") or 0):
                return False, f"JWT: Zugang von {rid} widerrufen"
        return True, "StudioLink-Token"

    def decide(self, req: dict) -> tuple[bool, str]:
        s = get_settings()
        action = req.get("action") or ""
        path = (req.get("path") or "").lstrip("/")
        user = req.get("user") or ""
        password = req.get("password") or ""
        ip = req.get("ip") or ""

        if action in ADMIN_ACTIONS and s.mtx_api_user and user and _consteq(user, s.mtx_api_user) and _consteq(password, s.mtx_api_pass):
            return True, "GUI-API-Zugang"

        token = extract_token(req)
        if token and action not in ADMIN_ACTIONS:
            ok, reason = self._jwt_allows(token, action, path)
            if ok:
                return True, reason
            # A JWT was clearly intended (user "jwt" or token field): don't
            # fall back to password matching with the token as password.
            if user in ("", "jwt"):
                return False, reason

        if user and internal_users_allow(self.gui_credentials, user=user, password=password, ip=ip, action=action, path=path):
            return True, "GUI-Zugangsdaten"
        if internal_users_allow(self.internal_users, user=user, password=password, ip=ip, action=action, path=path):
            return True, "authInternalUsers"
        if self.internal_users_loaded_at is None or self.internal_users_error:
            # Liste fehlt oder letzter Abruf schlug fehl (z. B. weil MediaMTX die GUI
            # noch nicht erreichte): sofort neu laden statt bis zu 15 s zu warten.
            if time.time() - self._last_users_attempt > 2:
                request_refresh()
        if self.internal_users_loaded_at is None:
            return False, "Benutzerliste noch nicht geladen"
        return False, "keine passende Berechtigung"


_state: AuthState | None = None
_task: asyncio.Task | None = None
_stop: asyncio.Event | None = None
_refresh_now: asyncio.Event | None = None


def get_auth_state() -> AuthState:
    global _state
    if _state is None:
        _state = AuthState()
    return _state


def request_refresh() -> None:
    """Call after changing credentials so the new list is used immediately."""
    if _refresh_now is not None:
        _refresh_now.set()


async def _loop(client: MediaMTXClient, sl: StudioLinkClient) -> None:
    s = get_settings()
    state = get_auth_state()
    state.load_cached_jwks()
    last_jwks = 0.0
    async with httpx.AsyncClient(timeout=5.0) as http:
        while not _stop.is_set():
            await state.refresh_gui_credentials()
            await state.refresh_internal_users(client)
            await state.refresh_reporters(sl)
            if time.time() - last_jwks > JWKS_REFRESH_SECONDS or state._jwks_wanted or not state.jwks:
                await state.refresh_jwks(http)
                state._jwks_wanted = False
                last_jwks = time.time()
            _refresh_now.clear()
            stop_wait = asyncio.create_task(_stop.wait())
            refresh_wait = asyncio.create_task(_refresh_now.wait())
            await asyncio.wait({stop_wait, refresh_wait}, timeout=s.mtx_auth_refresh_seconds, return_when=asyncio.FIRST_COMPLETED)
            for t in (stop_wait, refresh_wait):
                t.cancel()


def start_auth_task(client: MediaMTXClient, sl: StudioLinkClient) -> None:
    global _task, _stop, _refresh_now
    if _task is not None:
        return
    _stop = asyncio.Event()
    _refresh_now = asyncio.Event()
    _task = asyncio.create_task(_loop(client, sl))
    logger.info("MediaMTX-Auth-Cache gestartet.")


async def stop_auth_task() -> None:
    global _task, _stop
    if _stop is not None:
        _stop.set()
    if _task is not None:
        await _task
    _task = None
    _stop = None
