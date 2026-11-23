# CLAUDE.md – MediaMTX-GUI

Weboberfläche (FastAPI + Jinja2, SQLite) zur Verwaltung eines MediaMTX-Servers
der HSD. Seit November 2026 zusätzlich **Anmelde-Instanz für MediaMTX**
(`authMethod: http`) und **Verwaltungsoberfläche für StudioLink**
(Reporter, QR-Codes, Gerätetoken). Gegenstück: Repository `Studio-link`
(dort `CLAUDE.md`, `docs/architektur.md`, `docs/uebergabe.md`).

Sprache: Deutsch, formell. Kein Git-Repository auf dem Mac; Stand liegt
zusätzlich als `mediamtx-gui.bundle` vor. Ziel: eigenes GitHub-Repository.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `app/main.py` | App, Middleware, Router, Hintergrundaufgaben (Snapshots, Auth-Cache), Log-Filter für `/mtx/auth` |
| `app/config.py` | `Settings` (pydantic, `.env`): MediaMTX-API, StudioLink, `mtx_auth_secret`, Zeitzone |
| `app/deps.py` | `require_login`, `require_admin`, CSRF (`get_or_create_csrf_token`, `verify_csrf`), Clients |
| `app/mtx_auth.py` | `AuthState`, `decide()`: Entscheidungslogik für MediaMTX-Anmeldungen |
| `app/routers/mtx_auth.py` | `POST /mtx/auth?secret=…` (nur intern, nicht über den Proxy) |
| `app/studiolink_client.py` | Client für die Admin-API des StudioLink-Backends |
| `app/routers/studiolink.py` | `/studiolink…`: Reporter-CRUD, Widerruf, QR, Encoder-Token, Gerätetoken |
| `app/credentials_sync.py` | Zugangsdaten DB → MediaMTX `authInternalUsers`; stößt Auth-Cache-Refresh an |
| `app/templates/studiolink*.html` | Seiten des StudioLink-Moduls |
| `app/static/` | CSS/JS (responsive Navigation, Umschalter, QR-Zoom, Geräte-Vorlagen) |
| `reverse-proxy/` | Apache/nginx-Beispiele, **`/mtx/auth` blockieren** |
| `BETRIEB.md`, `README.md` | Betrieb und Installation |
| `REVIEW-finecomb-2026-11-02.md` | Sicherheitsaudit (19 Befunde, u. a. F-01/F-02 hoch) – Umsetzungsstand prüfen |

## Anmeldelogik (`mtx_auth.decide`)

Reihenfolge: (1) eigener API-Benutzer der GUI → (2) JWT (ES256, JWKS vom
StudioLink-Backend, Claim `mediamtx_permissions`; Widerruf über
`revokedBefore`; Gerätetoken über `jti`, gelöschte Geräte über
`devices_seen` bzw. Regel `iat + 1 < geladen`) → (3) Zugangsdaten aus der
GUI-Datenbank direkt (überleben MediaMTX-Neustarts) → (4) `authInternalUsers`
mit MediaMTX-Semantik (`any`, Klartext/`sha256:`, `ips`, `~regex`).
Fehlt der Cache, wird sofort nachgeladen (gedrosselt 2 s). Änderungen an
dieser Datei betreffen **jede** Verbindung zum MediaMTX – sorgfältig testen.

## Konventionen

- Formular-POSTs immer mit `verify_csrf`; Admin-Seiten mit
  `Depends(require_admin)`; Änderungen über `audit` protokollieren.
- Template-Texte nutzen teils Umschrift (ae/oe/ue) – beim Bearbeiten den
  Stil der Datei beibehalten.
- Zeitangaben über `display_timezone` (Paket `tzdata` ist nötig).
- Backend-Fehler aus StudioLink (`ValidationError` → HTTP 400) als
  Formularfehler anzeigen, nicht als 500.
- Geheimnisse (`secret`, Tokens) nie loggen; das Access-Log filtert `/mtx/auth`.

## Lokal ausführen

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # SECRET_KEY, ENCRYPTION_KEY, MTX_*, STUDIOLINK_* setzen
MEDIAMTX_GUI_DATA_DIR=./data uvicorn app.main:app --reload --port 8081
```

Es gibt keine automatisierten Tests. Mindestens `python -m compileall app`
ausführen und die betroffenen Seiten im Browser prüfen.

## Deployment

Server `Pub-test001`, Verzeichnis `~/mediamtx-gui`,
`docker compose up -d --build`, Container `mediamtx-gui`, Port 8081
(an `127.0.0.1` binden). `extra_hosts: host.docker.internal:host-gateway`
für den Zugriff auf das StudioLink-Backend (`:8787`). Details:
`Studio-link/docs/betrieb.md`.
