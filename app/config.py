"""Application configuration, loaded from environment variables / .env."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str
    encryption_key: str

    data_dir: str = os.environ.get("MEDIAMTX_GUI_DATA_DIR", "/data")

    mtx_api_url: str = "http://mediamtx:9997"
    mtx_api_user: str = ""
    mtx_api_pass: str = ""

    mtx_rtsp_host: str = "mediamtx"
    mtx_rtsp_port: int = 8554

    mtx_playback_url: str = ""

    public_host: str = "localhost"

    mtx_container_name: str = ""

    # Sperre fuer die Docker-Container-Steuerung. Standardmaessig aus: Die
    # GUI greift dann niemals auf /var/run/docker.sock zu, selbst wenn der
    # Socket versehentlich doch gemountet und MTX_CONTAINER_NAME gesetzt ist.
    docker_control_enabled: bool = False

    # --- StudioLink-Anbindung (optional) ---------------------------------
    # Basis-URL des StudioLink-Backends aus Sicht der GUI (Admin-API). Leer =
    # StudioLink-Seiten ausgeblendet.
    studiolink_api_url: str = ""
    # adminToken des Backends (config.json bzw. STUDIOLINK_ADMIN_TOKEN).
    studiolink_admin_token: str = ""
    # JWKS-URL fuer die Pruefung der App-/Geraete-Tokens. Leer = <api_url>/.well-known/jwks.json
    studiolink_jwks_url: str = ""

    # --- MediaMTX HTTP-Authentifizierung -----------------------------------
    # Geheimnis fuer den Endpunkt /mtx/auth. Leer = Endpunkt deaktiviert.
    # In mediamtx.yml: authHTTPAddress: http://<gui>:8080/mtx/auth?secret=<MTX_AUTH_SECRET>
    mtx_auth_secret: str = ""
    # Claim-Name fuer die Berechtigungen in den JWTs (wie authJWTClaimKey).
    mtx_jwt_claim_key: str = "mediamtx_permissions"
    # Aktualisierungsintervall fuer Benutzerliste/Reporter/JWKS-Cache.
    mtx_auth_refresh_seconds: int = 15

    # Zeitzone fuer angezeigte Zeitpunkte (Gueltigkeit von QR-Codes usw.).
    display_timezone: str = "Europe/Berlin"

    snapshot_interval_seconds: int = 15
    snapshot_timeout_seconds: int = 8

    session_max_age_minutes: int = 480

    # When true (the default), the session cookie gets the "Secure" flag and
    # HSTS is sent, i.e. the browser will only ever send it over HTTPS. Set
    # to false ONLY for local/dev testing without a TLS-terminating reverse
    # proxy in front of the app - never in a real deployment, or the session
    # cookie (and the login form's password) can be read off plain HTTP.
    force_secure_cookies: bool = True

    @field_validator("secret_key")
    @classmethod
    def _check_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY ist zu kurz (mindestens 32 Zeichen fuer eine sichere "
                "Session-Signatur). Mit 'openssl rand -hex 32' erzeugen."
            )
        return v

    @field_validator("encryption_key")
    @classmethod
    def _check_encryption_key(cls, v: str) -> str:
        from cryptography.fernet import Fernet

        try:
            Fernet(v.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "ENCRYPTION_KEY ist kein gueltiger Fernet-Schluessel. Mit "
                '\'python -c "from cryptography.fernet import Fernet; '
                "print(Fernet.generate_key().decode())\"' erzeugen."
            ) from exc
        return v

    @property
    def studiolink_enabled(self) -> bool:
        return bool(self.studiolink_api_url and self.studiolink_admin_token)

    @property
    def effective_jwks_url(self) -> str:
        if self.studiolink_jwks_url:
            return self.studiolink_jwks_url
        if self.studiolink_api_url:
            return self.studiolink_api_url.rstrip("/") + "/.well-known/jwks.json"
        return ""

    @property
    def jwks_cache_path(self) -> str:
        return str(Path(self.data_dir) / "studiolink-jwks.json")

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "mediamtx_gui.db")

    @property
    def snapshot_dir(self) -> str:
        return str(Path(self.data_dir) / "snapshots")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        Path(_settings.data_dir).mkdir(parents=True, exist_ok=True)
        Path(_settings.snapshot_dir).mkdir(parents=True, exist_ok=True)
    return _settings
