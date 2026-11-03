# Code-Review und Sicherheitsaudit – MediaMTX GUI (finecomb)

Datum: 02.11.2026 · Prüfer: Claude (finecomb-Checkliste) · Modus: nur lesend, exhaustiv, keine Fixes

## 0. Kurzfassung

**Ziel:** Ordner `mediamtx-gui` (FastAPI-Anwendung `app/`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `reverse-proxy/*`, `README.md`, `BETRIEB.md`). Kein Git-Repository vorhanden; der geprüfte Stand ist durch die Dateizeitstempel vom 21.10.2026 (jüngste Änderung `config.py`, `docker_control.py`, `server.html`) festgelegt. `.DS_Store`- und `__pycache__`-Dateien wurden nicht geprüft.

**Ergebnis:** 19 bestätigte Befunde (2 × Hoch, 7 × Mittel, 10 × Niedrig) und 3 unverifizierte Punkte. Die beiden schwerwiegendsten Befunde betreffen die Rechtetrennung zwischen „Benutzer“ und „Admin“: Ein normaler GUI-Benutzer kann über eine Pfad-Traversal in den Stream-Namen die globale MediaMTX-Konfiguration samt `authInternalUsers` (inklusive API-Zugang der GUI) auslesen (F-01), und jede Stream-Detailseite zeigt Nicht-Admins ohnehin ein Klartext-Passwort eines Sende-Zugangs sowie die vollständige Pfadkonfiguration mit Kamera-Passwörtern und SRT-Passphrasen (F-02). Beides wurde in einer isolierten Kopie gegen einen MediaMTX-Mock reproduziert.

| ID | Schwere | Evidenz | Titel |
|---|---|---|---|
| F-01 | **Hoch** | Reproduziert | Pfad-Traversal vom Stream-Namen in die MediaMTX-Control-API |
| F-02 | **Hoch** | Reproduziert | Nicht-Admins sehen Klartext-Zugangsdaten und vollständige Pfadkonfiguration |
| F-03 | Mittel | Statisch bestätigt | Login-Sperre an Proxy-IP und Benutzername gebunden – Aussperren aller Nutzer ohne Login |
| F-04 | Mittel | Statisch bestätigt | Unauthentifiziertes, unbegrenztes Wachstum von Audit-Tabelle und Sperr-Speicher |
| F-05 | Mittel | Werkzeug-bestätigt | Veraltete Abhängigkeiten mit bekannten Schwachstellen (u. a. unauthentifiziert erreichbares Formular-Parsing) |
| F-06 | Mittel | Statisch bestätigt (GUI-Seite) | `decrypt()` liefert bei falschem Schlüssel `""` – Sync schreibt leere Passwörter nach MediaMTX |
| F-07 | Mittel | Statisch bestätigt | Zugangs-Benutzernamen ohne Validierung – Kollision mit manuellen Einträgen (z. B. API-User) |
| F-08 | Mittel | Statisch bestätigt | Credential-Sync nicht atomar: Divergenz, blockierende Fehleinträge, Lost Update, Audit-Lücke |
| F-09 | Mittel | Statisch bestätigt | Container läuft als root, ffmpeg dekodiert fremde Medien neben allen Geheimnissen; Port 8080 offen gebunden |
| F-10 | Niedrig | Reproduziert | Erzwungener Passwortwechsel wird nicht durchgesetzt |
| F-11 | Niedrig | Statisch bestätigt | Sitzungen nicht widerrufbar; Klartext-Passwörter im (nur signierten) Session-Cookie; Logout per GET |
| F-12 | Niedrig | Statisch bestätigt | Kollision der Snapshot-Dateinamen hebelt Pfad-Beschränkung für Vorschaubilder aus |
| F-13 | Niedrig | Statisch bestätigt | Download-/Playback-Links zeigen auf interne Adressen bzw. falsches Schema; keine URL-Kodierung |
| F-14 | Niedrig | Statisch bestätigt | Routen-Überschattung bestimmter Stream-Namen; unbehandelte API-Fehler führen zu HTTP 500 |
| F-15 | Niedrig | Statisch bestätigt | Snapshot-Job belegt Leser-Slots, ignoriert >1000 Pfade, räumt nie auf |
| F-16 | Niedrig | Statisch bestätigt | Admin kann sich selbst deaktivieren; „leer = alle Streams“ ist ein fail-open-Standard |
| F-17 | Niedrig | Statisch bestätigt | Dokumentation widerspricht dem Code |
| F-18 | Niedrig | Statisch bestätigt | Geheimnisse in Logs, Prozessliste und Audit-Details |
| F-19 | Niedrig | Statisch bestätigt | Wartbarkeit: keine Tests, veraltete APIs, kein Migrationspfad, unpinned Base-Image |
| U-1 | (Hoch, falls bestätigt) | Unverifiziert | Nach MediaMTX-Neustart sind per API angelegte Streams/Zugänge weg; kein Resync |
| U-2 | – | Unverifiziert | Snapshot-Passwort in ffmpeg-Fehlermeldung bei älteren ffmpeg-Versionen |
| U-3 | – | Unverifiziert | Gibt MediaMTX `pass`-Felder über `/v3/config/global/get` im Klartext zurück? (bestimmt Reichweite von F-01) |

### Bewusst übersprungene bzw. eingeschränkte Prüfungen

| Prüfung | Grund | Was fehlt |
|---|---|---|
| Verhalten gegen echtes MediaMTX (Persistenz von API-Änderungen, Rückgabe von Passwörtern, Semantik leerer Passwörter) | Keine MediaMTX-Instanz in der Sandbox; nur ein HTTP-Mock | Test gegen `bluenviron/mediamtx` in der eingesetzten Version (betrifft U-1, U-3, F-06) |
| Docker-Build und Image-Scan (Trivy/Grype) | Nicht ausgeführt (kein Docker-Daemon in der Sandbox) | `docker build` + Image-Scan auf dem Server |
| Laufzeit auf Python 3.12 / Debian-ffmpeg des Images | Sandbox hat Python 3.13 und ffmpeg 6.1 (Ubuntu) | Reproduktion im echten Image (betrifft U-2) |
| Aktive Tests gegen eine Live-Instanz | Keine Adresse und keine Autorisierung für aktive Tests | – (nicht erforderlich für den Code-Review) |
| Reverse-Proxy-Konfigurationen | Nur statisch gelesen, nicht gestartet | – |

---

## 1. Faktische Basis

**Sprachen/Formate:** Python 3.12 (FastAPI 0.115.0, Starlette 0.38.6, SQLAlchemy 2.0, passlib/bcrypt, httpx, cryptography/Fernet, docker-SDK), Jinja2-Templates (Autoescape aktiv über `Jinja2Templates`), Vanilla-JavaScript, CSS, Dockerfile, Compose-YAML, nginx- und Caddy-Konfiguration, SQLite.

**Laufzeit/Deployment:** Ein Container (`uvicorn app.main:app --host 0.0.0.0 --port 8080`, ein Prozess), SQLite unter `/data`, asyncio-Hintergrundtask für Snapshots (ffmpeg-Subprozesse), Kommunikation mit MediaMTX über Control-API (Port 9997), RTSP (8554) und Playback (9996). TLS terminiert ein vorgeschalteter Reverse-Proxy (Caddy/nginx), der laut Beispielen im selben Docker-Netz läuft.

**Objekte:**
- Öffentliche Einstiegspunkte: `GET/POST /login`, `GET /logout`, `GET /healthz`, `/static/*`.
- Angemeldet (jede Rolle): `GET /`, `GET /streams/{name:path}`, `GET /streams/{name:path}/snapshot.jpg`, `GET /streams/{name:path}/recordings`, `GET/POST /change-password`.
- Nur Admin: Streams anlegen/bearbeiten/löschen, `/credentials*`, `/users*`, `/server*`, `/audit-log`.
- Geteilter Zustand: SQLite (Benutzer, Stream-Metadaten, verschlüsselte Zugänge, Registry verwalteter Benutzernamen, Audit-Log), prozesslokale Dicts (`ratelimit._failed_attempts`, `snapshots.state`), Snapshot-Dateien, MediaMTX-Globalkonfiguration (`authInternalUsers`).
- Externe Seiteneffekte: PATCH/POST/DELETE an die MediaMTX-API, ffmpeg-Prozesse, optional Docker-Socket.

**Bedrohungsmodell (Annahmen):**
- Angreifer A1 – unauthentifiziert im Internet (erreicht den Reverse-Proxy, ggf. Port 8080 direkt).
- Angreifer A2 – GUI-Konto mit Rolle „Benutzer“ (laut UI „nur Ansicht, ggf. eingeschränkt“; z. B. Studierende).
- Angreifer A3 – Inhaber eines Sende-Zugangs bzw. anonymer Publisher, der Mediendaten liefert, die ffmpeg dekodiert.
- Admins gelten als vertrauenswürdig; README dokumentiert ausdrücklich, dass Admin ≈ Codeausführung im MediaMTX-Container (runOn*-Hooks). Das wird nicht als Befund gewertet.
- Schutzgüter: MediaMTX-Steuerung (API-Zugang), Sende-/Lese-Zugänge, Kamera-/Quell-Zugangsdaten, `SECRET_KEY`/`ENCRYPTION_KEY`, Verfügbarkeit der GUI.

---

## 2. Befunde

### F-01 · Hoch · Reproduziert – Pfad-Traversal vom Stream-Namen in die MediaMTX-Control-API

**Ort:** `app/routers/streams.py:197–264` (`stream_detail`), außerdem `:93–130`, `:133–167`, `:170–194`; `app/mtx_client.py:89–117`.

**Symptom und Ursache:** Der Stream-Name aus dem URL-Pfad (`{name:path}`) wird ohne Validierung per f-String in API-Pfade eingesetzt, z. B. `f"/v3/config/paths/get/{name}"`. Uvicorn dekodiert `%2F` zu `/`, und httpx normalisiert anschließend `..`-Segmente beim Zusammenbau der Ziel-URL. Ein Name `../../global/get` wird damit zu `GET /v3/config/global/get`. Die Detailseite rendert das Ergebnis vollständig als `conf_pretty` (`stream_detail.html:67`). `validate_path_name()` existiert, wird aber nur beim Anlegen (`new_stream_submit`) verwendet, nicht auf den URL-Parametern. Die einzige Hürde ist `user.can_see_path(name)` (`models.py:36–40`); für Benutzer ohne Pfadbeschränkung (Standard bei leerem Feld) gibt sie für jeden Namen `True` zurück.

**Auslöser:** Angemeldetes Konto mit Rolle „Benutzer“ ohne Pfadbeschränkung (oder Admin). Anfrage `GET /streams/..%2F..%2Fglobal%2Fget` (prozentkodiert, damit Browser/Clients das `..` nicht vorher entfernen).

**Evidenz (Reproduktion):** Isolierte Kopie mit Mock-MediaMTX auf `127.0.0.1:19997`, Konto `student` mit Rolle „user“ und leerem `allowed_paths`:
```
== student traversal status: HTTP/1.1 200 OK | API-SECRET-PW leaked: True | PUBLISH-SECRET leaked: True
MOCK GET /v3/config/global/get
```
Der Mock-Server bestätigt, dass die GUI tatsächlich `/v3/config/global/get` aufgerufen hat.

**Auswirkung:** Ein „Nur-Ansicht“-Benutzer liest die komplette globale MediaMTX-Konfiguration. Ob darin Passwörter im Klartext stehen, hängt von MediaMTX ab (U-3) – die GUI selbst setzt das allerdings voraus: `credentials_sync.py:50–66` liest `authInternalUsers` zurück und schreibt die fremden Einträge inklusive `pass` unverändert wieder, und die README legt den API-Zugang mit Klartext-Passwort an. Trifft das zu, erhält der Benutzer den API-Zugang `mediamtx-gui` (Rechte `api`, `read`) und alle Sende-Zugänge. Mit dem API-Zugang (sofern Port 9997 für ihn erreichbar ist) kann er Pfade mit `runOnReady`-Hooks anlegen, also Befehle im MediaMTX-Container ausführen; mit Sende-Zugängen kann er laufende Streams übernehmen (`overridePublisher` ist standardmäßig an). Weitere GET-Endpunkte der API (Sessions, Verbindungen inkl. Client-IPs) sind auf demselben Weg lesbar. Für Admins ermöglichen die Edit-/Delete-Routen beliebige POST/DELETE-Ziele, was aber innerhalb ihrer ohnehin vollen Rechte liegt.

**Empfehlung:** Den Namen an der Autoritätsgrenze validieren: in allen Routen mit `{name:path}` zuerst `validate_path_name()` aufrufen (bzw. eine Variante, die `.`/`..`-Segmente und `%` ausdrücklich ablehnt) und zusätzlich in `MediaMTXClient` jeden Namen mit `urllib.parse.quote(name, safe="/")` kodieren und `..`-Segmente ablehnen. Abnahme: `/streams/..%2F..%2Fglobal%2Fget` liefert 404/Redirect, und der Mock sieht keinen Aufruf außerhalb von `/v3/config/paths/*` bzw. `/v3/paths/*`.

### F-02 · Hoch · Reproduziert – Nicht-Admins sehen Klartext-Zugangsdaten und vollständige Pfadkonfiguration

**Ort:** `app/routers/streams.py:239–262`, `app/templates/stream_detail.html:49–68`, `app/links.py:52–54`.

**Symptom und Ursache:** `stream_detail` ist nur durch `require_login` geschützt. Für die Verbindungs-Links wird der erste nicht-systeminterne Zugang mit passendem Pfad genommen, sein Passwort entschlüsselt und in die angezeigten URLs eingesetzt (`rtsp://user:pass@…`, `?user=…&pass=…`, SRT-`streamid`). Welche Rechte dieser Zugang hat (publish/read/playback), wird nicht geprüft. Zusätzlich wird die vollständige Pfadkonfiguration ausgegeben – darin stehen typischerweise Quell-URLs mit Kamera-Zugangsdaten, `srtPublishPassphrase`/`srtReadPassphrase`, `runOn*`-Befehle usw. Das widerspricht der Rollenbeschreibung „Benutzer (nur Ansicht)“ in `user_form.html:23` und in README/BETRIEB.

**Auslöser:** Jedes aktive GUI-Konto, das den Pfad sehen darf, also auch auf einzelne Streams beschränkte Studierendenkonten.

**Evidenz (Reproduktion):**
```
== student /streams/cam1: password-in-links: ['rtsp://encoder-halle1:j9LXTaEf…@localhost:8554/cam1']  conf secrets: True True
```
Der Zugang `encoder-halle1` hatte nur das Recht `publish`; `CAMERA-PW` (aus `source`) und `SRT-SECRET` (aus `srtReadPassphrase`) wurden ausgeliefert.

**Auswirkung:** Ein Nur-Ansicht-Konto erhält Sende-Zugangsdaten und kann damit Streams überschreiben oder eigene Inhalte einspeisen. Auch Zugangsdaten externer Kameras/Quellen werden offengelegt. Die Passwörter werden zudem auf jeder Seitenansicht neu ausgeliefert, obwohl die UI sie als „nur jetzt angezeigt“ beschreibt.

**Empfehlung:** Für Nicht-Admins Links ohne Zugangsdaten bauen (Platzhalter `<benutzer>:<passwort>`), und für Admins nur einen Zugang mit passender Aktion je Link verwenden (Publish-Links nur mit `publish`-Zugang usw.). `conf_pretty` nur für Admins rendern oder für Nicht-Admins auf eine Allowlist unkritischer Felder reduzieren. Abnahme: Als Rolle „user“ enthält die Detailseite weder `@` in Protokoll-URLs noch `pass=` noch Felder der Pfadkonfiguration außerhalb der Allowlist.

### F-03 · Mittel · Statisch bestätigt – Login-Sperre an Proxy-IP und Benutzername gebunden

**Ort:** `app/routers/auth.py:37–57`, `app/ratelimit.py`, `Dockerfile:26`, `reverse-proxy/*`.

**Ursache:** Die Sperre verwendet `request.client.host`. Uvicorn übernimmt `X-Forwarded-For` nur von `--forwarded-allow-ips` (Standard `127.0.0.1`); der Start-Befehl im Dockerfile setzt das nicht, und der Proxy läuft laut Beispielen als eigener Container (andere IP). Damit ist `client.host` für alle Nutzer die IP des Proxys. Zusätzlich gibt es einen Schlüssel je Benutzername.

**Auslöser:** Angreifer A1 ruft `/login` auf (liefert CSRF-Token und Cookie) und sendet fünf falsche Passwörter, einmal alle 15 Minuten.

**Auswirkung:** Hinter dem Proxy werden damit alle Benutzer gleichzeitig für 15 Minuten gesperrt; ohne Proxy-Problem lässt sich immer noch gezielt das Konto `admin` (vorhersagbarer Name) dauerhaft aussperren. Gleichzeitig ist der Schutz gegen verteilte Brute-Force schwach (die IP-Komponente ist wirkungslos). Im Audit-Log fehlt die Client-IP, sodass solche Angriffe nicht nachvollziehbar sind.

**Empfehlung:** Uvicorn mit `--proxy-headers --forwarded-allow-ips=<Proxy-Netz>` starten bzw. die Proxy-IP explizit konfigurierbar machen; die Benutzernamen-Sperre durch eine progressive Verzögerung statt einer harten Sperre ersetzen; die IP im Audit-Eintrag speichern.

### F-04 · Mittel · Statisch bestätigt – Unauthentifiziertes, unbegrenztes Wachstum von Audit-Tabelle und Sperr-Speicher

**Ort:** `app/routers/auth.py:44, 56`, `app/ratelimit.py:15–46`, `app/models.py:107`.

**Ursache:** Jeder fehlgeschlagene *und* jeder bereits gesperrte Login-Versuch schreibt eine Audit-Zeile mit dem vom Angreifer gewählten Benutzernamen. SQLite erzwingt `String(64)` nicht, und Formularfelder dürfen bei Starlette/python-multipart bis etwa 1 MB groß sein. `_failed_attempts` bekommt pro neuem Benutzernamen einen Eintrag, der nur beim erneuten Zugriff auf genau diesen Schlüssel bereinigt wird.

**Auswirkung:** Ein unauthentifizierter Angreifer kann mit wenigen Requests pro Sekunde Datenträger und Speicher des Containers füllen; das Audit-Log der letzten 200 Einträge wird zudem mit Rauschen überschrieben (Spuren verwischen).

**Empfehlung:** Benutzernamen vor jeder Verarbeitung auf z. B. 64 Zeichen kürzen bzw. ablehnen; „login_blocked“ nicht protokollieren oder aggregieren; `_failed_attempts` zeitbasiert vollständig bereinigen oder in der Größe begrenzen; Audit-Retention einführen.

### F-05 · Mittel · Werkzeug-bestätigt – Veraltete Abhängigkeiten mit bekannten Schwachstellen

**Ort:** `requirements.txt`. **Evidenz:** `pip-audit` (Abfrage am 02.11.2026 gegen PyPI/OSV) auf der aufgelösten Umgebung:

| Paket | Version | Advisories | Behoben ab | Relevanz für diese App |
|---|---|---|---|---|
| python-multipart | 0.0.9 | 7 (u. a. GHSA-59g5-xgcq-4qw3, CVE-2026-40347, CVE-2026-53539) | 0.0.31 | Hoch – DoS beim Parsen von Formularen, unauthentifiziert über `POST /login` erreichbar |
| starlette (über fastapi 0.115.0) | 0.38.6 | 7 (u. a. CVE-2024-47874, CVE-2025-54121, CVE-2026-48710) | 1.3.1 | Hoch – Multipart-DoS, Form-Limits, Host-Header; `StaticFiles`-Windows-Pfadproblem nicht relevant |
| jinja2 | 3.1.4 | 3 (CVE-2024-56201, CVE-2025-27516, …) | 3.1.6 | Gering – Sandbox wird nicht genutzt, Templates sind nicht angreiferkontrolliert |
| cryptography | 43.0.1 | 6 (u. a. CVE-2024-12797) | 49.0.0 | Gering – nur Fernet wird genutzt; X.509/TLS-Pfade nicht |
| python-dotenv | 1.0.1 | 1 (CVE-2026-28684) | 1.2.2 | Nicht relevant – `set_key()` wird nicht verwendet |

Zusätzlich: `passlib 1.7.4` ist seit Jahren unmaintained und erzeugt mit `bcrypt 4.2.0` beim Start einen Traceback („error reading bcrypt version“, in der Reproduktion beobachtet); mit bcrypt ≥ 5 bricht die Kombination. Einzelne Advisories wurden nicht praktisch ausgenutzt.

**Empfehlung:** FastAPI/Starlette und python-multipart auf aktuelle Versionen heben (FastAPI-Version so wählen, dass Starlette ≥ 1.3.1 erlaubt ist), jinja2 ≥ 3.1.6, cryptography aktuell; passlib durch direkte Nutzung von `bcrypt` oder `argon2-cffi` ersetzen; ein Lockfile mit Hashes (`pip-compile --generate-hashes`) und ein regelmäßiges `pip-audit` einführen.

### F-06 · Mittel · Statisch bestätigt (GUI-Seite) – `decrypt()` liefert `""` bei falschem Schlüssel; Sync schreibt leere Passwörter

**Ort:** `app/crypto_utils.py:36–42`, `app/credentials_sync.py:60`, `app/snapshots.py:46–54, 65`.

**Ursache:** `InvalidToken` wird verschluckt und als leerer String zurückgegeben. Wird `ENCRYPTION_KEY` beim Restore/Update versehentlich geändert oder geht er verloren (BETRIEB.md erwähnt das Szenario ausdrücklich), schreibt die nächste Synchronisation alle GUI-Zugänge mit `"pass": ""` nach MediaMTX, ohne Fehlermeldung. Der Snapshot-Job verbindet sich dann ohne Zugangsdaten.

**Auswirkung:** Je nach MediaMTX-Semantik leerer Passwörter (nicht verifiziert) können Sende-/Lesezugänge danach mit bekanntem Benutzernamen und leerem Passwort genutzt werden; mindestens aber funktionieren alle Encoder still nicht mehr. Ein Sicherheitsmechanismus fällt hier offen statt geschlossen aus.

**Empfehlung:** `decrypt()` soll bei `InvalidToken` eine Ausnahme werfen; `sync_credentials_to_mtx` soll abbrechen, wenn ein Passwort nicht entschlüsselbar ist; beim Start einmal eine Probe-Entschlüsselung durchführen und laut warnen.

### F-07 · Mittel · Statisch bestätigt – Zugangs-Benutzernamen ohne Validierung; Kollision mit manuellen Einträgen

**Ort:** `app/routers/credentials.py:43–49`, `app/credentials_sync.py:30–66`.

**Ursache:** Der Benutzername eines Stream-Zugangs wird nur auf „nicht leer“ und Eindeutigkeit innerhalb der GUI-Datenbank geprüft. Der Sync entfernt jeden `authInternalUsers`-Eintrag, dessen Name in der (nur wachsenden) Registry steht, und ersetzt ihn durch den GUI-Eintrag.

**Auswirkung:**
- Ein Admin legt (versehentlich) einen Zugang namens `mediamtx-gui` (= `MTX_API_USER`) an: der manuelle Eintrag mit Recht `api` wird überschrieben, die GUI sperrt sich selbst aus der API aus. Wiederherstellung nur durch Handarbeit in `mediamtx.yml`.
- Ein Zugang namens `any` entfernt *alle* `any`-Einträge (in der Standard-`mediamtx.yml` die anonymen Rechte inkl. API-Zugriff von localhost) und ändert damit die Zugriffspolitik grundlegend.
- Da die Registry nie schrumpft, werden später von Hand angelegte Einträge mit einem einmal verwendeten Namen bei jedem Sync stillschweigend gelöscht – im Widerspruch zur README-Zusage, manuelle Einträge „nicht anzutasten“.
- Sonderzeichen in Namen/Passwörtern (`:`, `@`, `&`, `/`) erzeugen kaputte Links (F-13).

**Empfehlung:** Benutzernamen auf `^[A-Za-z0-9._-]{1,64}$` beschränken, `any`, `MTX_API_USER` und Namen bereits vorhandener, nicht verwalteter Einträge ablehnen (vor dem Anlegen `get_global_config()` prüfen).

### F-08 · Mittel · Statisch bestätigt – Credential-Sync nicht atomar

**Ort:** `app/routers/credentials.py:66–75, 124–132, 160–169`, `app/credentials_sync.py:45–66`, `app/routers/server.py:98–101`.

**Ursachen und Folgen:**
1. Die lokale Änderung wird committed, bevor MediaMTX sie annimmt. Lehnt MediaMTX ab (z. B. ungültige IP/CIDR in `ips`, ungültiger Pfad – beide werden nicht validiert), bleibt der fehlerhafte Datensatz gespeichert, und **jeder** weitere Sync scheitert, bis jemand genau diesen Eintrag korrigiert. Die README empfiehlt als Abhilfe „eine beliebige Kleinigkeit erneut speichern“, was in diesem Fall nicht hilft.
2. Beim Löschen eines Zugangs, dessen Sync scheitert, bleibt der Zugang in MediaMTX aktiv, obwohl die GUI ihn als gelöscht zeigt.
3. In allen Fehlerzweigen wird `log_action` nicht aufgerufen – lokal wirksame Änderungen fehlen im Audit-Log.
4. Read-modify-write von `authInternalUsers` (und beim Protokoll-Schalter) ohne Sperre: zwei gleichzeitige Admin-Aktionen oder eine parallele Änderung über „Erweiterte Konfiguration“ überschreiben sich gegenseitig (Lost Update).
5. `edit_credential_submit` ändert das ORM-Objekt vor der Passwort-Längenprüfung; der Abbruch macht keinen Rollback. Die Änderung wird zwar nicht committed, aber nur, weil die Session danach geschlossen wird – fragil.

**Empfehlung:** IPs mit `ipaddress.ip_network()` und Pfade mit `validate_path_name()` prüfen; Sync vor dem Commit ausführen bzw. bei Fehlschlag zurückrollen; Audit-Eintrag in allen Zweigen; prozessweiten `asyncio.Lock` um alle Read-modify-write-Operationen auf die Globalkonfiguration.

### F-09 · Mittel · Statisch bestätigt – Container als root; ffmpeg dekodiert fremde Medien; Port 8080 offen

**Ort:** `Dockerfile` (kein `USER`), `docker-compose.yml:21–22`, `app/snapshots.py:63–107`.

**Ursache und Auswirkung:** Der Snapshot-Job dekodiert alle 15 s die Mediendaten jedes Online-Streams mit ffmpeg – also Daten, die Publisher (A3, ggf. anonym) liefern. ffmpeg hat regelmäßig Speicherfehler in Decodern. Ein erfolgreicher Exploit läuft als root im GUI-Container, in dem `SECRET_KEY` (Sitzungen fälschen = Admin), `ENCRYPTION_KEY`, die Datenbank und der API-Zugang liegen. Zusätzlich veröffentlicht die Compose-Datei `8080:8080` auf allen Interfaces; die App ist damit direkt per HTTP erreichbar (Passwort im Klartext übertragbar, Proxy-Header-Logik umgangen). Die Doku empfiehlt `127.0.0.1:8080:8080`, die ausgelieferte Datei setzt es aber nicht.

**Empfehlung:** Nicht-root-Benutzer im Dockerfile (`useradd -r app && chown /data`, `USER app`); in Compose `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, tmpfs für `/tmp`; ffmpeg auf das nötige Protokoll beschränken (`-protocol_whitelist rtsp,tcp,udp,rtp`) und mit Ressourcenlimits starten; Port standardmäßig an `127.0.0.1` binden oder ganz weglassen, wenn der Proxy im selben Netz läuft.

### F-10 · Niedrig · Reproduziert – Erzwungener Passwortwechsel wird nicht durchgesetzt

**Ort:** `app/deps.py:43–52`, `app/routers/auth.py:65–66`. `must_change_password` führt nur nach dem Login zu einem Redirect; alle anderen Routen prüfen das Flag nicht. Reproduktion: Direkt nach dem Login mit dem Initialpasswort liefert `GET /users` HTTP 200. Konten mit von Admins übermittelten (und im Flash/Chat weitergegebenen) Passwörtern bleiben so dauerhaft nutzbar. BETRIEB.md beschreibt den Wechsel als „erzwungen“. **Empfehlung:** In `require_login` bei gesetztem Flag auf `/change-password` umleiten (Ausnahmen: diese Route und `/logout`). Außerdem verlangt `change_password_submit` kein neues ≠ altes Passwort und ist nicht rate-limitiert.

### F-11 · Niedrig · Statisch bestätigt – Sitzungsverwaltung

**Ort:** `app/main.py:27–33`, `app/routers/auth.py:70–78`, `app/templating.py:13–16`, Flash-Aufrufe in `users.py`/`credentials.py`.
- Starlette-Sessions sind clientseitige, signierte, aber **unverschlüsselte** Cookies. Logout, Passwortänderung und Passwort-Reset durch einen Admin machen ein bereits kopiertes Cookie nicht ungültig (bis zu 8 h gültig). Nur Deaktivieren/Löschen des Kontos wirkt sofort.
- Neu generierte Klartext-Passwörter werden als Flash-Nachricht in dieses Cookie geschrieben (Base64-JSON, für jeden mit Zugriff auf das Cookie lesbar), bis die nächste Seite gerendert wird.
- `GET /logout` ist CSRF-anfällig (geringe Auswirkung). Beim Login wird die Session nicht neu aufgesetzt.

**Empfehlung:** Einen `session_version`-Zähler am Benutzer speichern, in die Session schreiben und in `get_current_user` vergleichen (Erhöhung bei Passwortwechsel/Reset/Logout-all); Einmalpasswörter nicht über das Cookie, sondern serverseitig (DB-Eintrag mit kurzer Lebensdauer) anzeigen; Logout als POST mit CSRF-Token.

### F-12 · Niedrig · Statisch bestätigt – Kollision der Snapshot-Dateinamen

**Ort:** `app/snapshots.py:57–60`, `app/routers/dashboard.py:75–82`. Die Bereinigung entfernt `/` und andere Zeichen, ohne die Abbildung eindeutig zu machen: `cams/eingang` und `camseingang` landen beide in `camseingang.jpg`. Ein Benutzer, der nur `camseingang` sehen darf, erhält das Vorschaubild von `cams/eingang`, sobald dieses zuletzt geschrieben wurde. Ad-hoc-Pfade (nicht über die GUI angelegt) können beliebige Zeichen enthalten; `isalnum()` lässt außerdem Unicode durch. **Empfehlung:** Dateiname = SHA-256 des Pfadnamens.

### F-13 · Niedrig · Statisch bestätigt – Falsche bzw. interne Links

**Ort:** `app/mtx_client.py:172–176`, `app/routers/recordings.py:34–38`, `app/links.py:52–83`.
- Der „Herunterladen“-Link für Aufnahmen basiert auf `MTX_PLAYBACK_URL` (z. B. `http://mediamtx:9996`), also auf einer nur im Docker-Netz auflösbaren Adresse. Im Browser funktioniert er nicht, und interne Hostnamen werden offengelegt. Zusätzlich verlangt der Playback-Server Zugangsdaten mit `playback`-Recht, die der Link nicht enthält.
- Der Playback-Link in `links.py:83` ist immer `http://`, auch bei `playbackEncryption`.
- Benutzername, Passwort und Pfad werden ohne URL-Kodierung eingesetzt; manuell vergebene Passwörter mit `@ : / & ?` erzeugen fehlerhafte oder anders interpretierte URLs.

**Empfehlung:** Download über einen authentifizierten Proxy-Endpunkt der GUI ausliefern (der die Pfadberechtigung prüft) oder eine konfigurierbare öffentliche Playback-Basis-URL einführen; `urllib.parse.quote`/`urlencode` verwenden.

### F-14 · Niedrig · Statisch bestätigt – Routen-Überschattung und unbehandelte API-Fehler

**Ort:** `app/main.py:118–130`, `app/routers/streams.py`. Weil Stream-Namen `/` enthalten dürfen und die spezifischen Routen vor der Detailroute registriert sind, sind Streams namens `new` oder `…/edit`, `…/delete`, `…/recordings` nicht über ihre Detailseite erreichbar (`/streams/foo/edit` öffnet stattdessen das Bearbeitungsformular von `foo`). Außerdem fangen `edit_stream_form` (Z. 101), `new_stream_submit` (Z. 62) und `stream_detail` (Z. 209) `MediaMTXError` aus `get_path_config` nicht ab; bei nicht erreichbarer API entsteht HTTP 500. CSRF-Fehler (400) werden als rohes JSON statt als Fehlerseite ausgegeben. **Empfehlung:** Reservierte Endsegmente in `validate_path_name` verbieten; Fehler einheitlich abfangen.

### F-15 · Niedrig · Statisch bestätigt – Nebenwirkungen des Snapshot-Jobs

**Ort:** `app/snapshots.py:110–134`, `app/routers/dashboard.py:30`. Jeder ffmpeg-Pull ist für MediaMTX ein regulärer Leser: Bei `maxReaders: 1` blockiert der Snapshot den einzigen Slot, `runOnRead`-Hooks werden ausgelöst und Leserstatistiken verfälscht. Die Pfadliste wird nur bis 1000 Einträge gelesen (Dashboard ebenso). Snapshot-Dateien gelöschter Streams und die Einträge in `state.last_error`/`last_success` werden nie entfernt; ein Stream, der später unter demselben Namen neu angelegt wird, zeigt zunächst ein altes Bild. **Empfehlung:** Snapshots nur für Pfade ohne bzw. mit ausreichendem `maxReaders`, Paginierung, Aufräumen beim Löschen.

### F-16 · Niedrig · Statisch bestätigt – Rollen-Randfälle

**Ort:** `app/routers/users.py:97–103`, `app/models.py:36–40`. Ein Admin darf sich zwar nicht selbst herabstufen oder löschen, aber selbst deaktivieren (Häkchen „Konto aktiv“ entfernen) – ist er der einzige Admin, sperrt er die Verwaltung aus, und eine Wiederherstellung ist nur über die Datenbank möglich. Außerdem bedeutet ein leeres `allowed_paths` für Rolle „Benutzer“ „alle Streams“ (fail-open); zusammen mit F-01/F-02 vergrößert das die Angriffsfläche. Werte in `allowed_paths` werden nicht validiert (Tippfehler bleiben unbemerkt). **Empfehlung:** Selbstdeaktivierung und Entfernen des letzten aktiven Admins verhindern; für neue Benutzer standardmäßig „keine Streams“ und explizit „alle“ als Option.

### F-17 · Niedrig · Statisch bestätigt – Dokumentation widerspricht dem Code

- README und BETRIEB verweisen auf `docker-compose.example.yml` („nach docker-compose.yml kopieren“); im Ordner existiert nur `docker-compose.yml`.
- BETRIEB §6: Passwortänderung „wird beim ersten Login erzwungen“ – nicht durchgesetzt (F-10).
- README: „Sperre nach 5 Fehlversuchen je Benutzername/IP“ – hinter dem Proxy faktisch je Proxy (F-03).
- README-Funktionsliste nennt „MediaMTX-Docker-Container starten/stoppen/neustarten“, obwohl diese Funktion standardmäßig gesperrt ist.
- BETRIEB §7 empfiehlt `docker restart mediamtx` als unkritisch; siehe U-1.
- README-Troubleshooting („Kleinigkeit erneut speichern“) hilft bei einem fehlerhaften Eintrag nicht (F-08).
- `snapshots.py:10–11` verweist auf ein nicht existierendes Modul `streams_service`; `SNAPSHOT_SERVICE_USER` ist doppelt definiert (`snapshots.py:32`, `credentials_sync.py:18`).

### F-18 · Niedrig · Statisch bestätigt – Geheimnisse in Logs, Prozessliste und Audit-Details

- Das initiale Admin-Passwort steht dauerhaft im Container-Log (`main.py:79–84`) und damit in jedem Log-Shipping/Backup; mitigiert durch den (nicht erzwungenen, F-10) Passwortwechsel.
- Das Snapshot-Passwort steht in der ffmpeg-Kommandozeile (`snapshots.py:65–74`) und ist für jeden Prozess im Container über `/proc/*/cmdline` sichtbar.
- `log_action(..., details=json.dumps(conf))` (`streams.py:88, 165`) speichert vollständige Pfadkonfigurationen inklusive Passphrasen und Quell-Zugangsdaten im Audit-Log.

**Empfehlung:** Initialpasswort in eine Datei mit Rechten 0600 in `/data` schreiben statt ins Log; RTSP-Zugangsdaten nicht über argv übergeben (ffmpeg unterstützt keine Env-Variable dafür – alternativ einen auf das GUI-Container-IP beschränkten Leseaccount ohne Passwort nutzen); Audit-Details vor dem Speichern um sensible Schlüssel bereinigen.

### F-19 · Niedrig · Statisch bestätigt – Wartbarkeit und Betrieb

Keine automatisierten Tests vorhanden (Testabdeckung 0 %). `@app.on_event` ist in FastAPI deprecated (Lifespan verwenden), `datetime.utcnow()` ist ab Python 3.12 deprecated (naive Zeitstempel). Kein Schemamigrationswerkzeug (`create_all` ändert bestehende Tabellen nicht; in BETRIEB dokumentiert). Base-Image `python:3.12-slim` und apt-Pakete sind nicht gepinnt (nicht reproduzierbar, ffmpeg-Version ändert sich still). Synchrone SQLAlchemy-Aufrufe in `async def`-Routen blockieren die Event-Loop. Die Doppelverwaltung von `SNAPSHOT_SERVICE_USER` und die verstreuten lokalen Imports erschweren Änderungen.

---

## 3. Unverifizierte Punkte

**U-1 (hohe Priorität zur Klärung) – Verlust der API-Konfiguration nach MediaMTX-Neustart.** Der Code geht selbst davon aus, dass ein Neustart „nicht persistierte Pfad-Configs verwirft“ (`streams.py:73–79`). Werden per Control-API vorgenommene Änderungen von MediaMTX nicht in `mediamtx.yml` zurückgeschrieben, verschwinden nach `docker restart mediamtx` (den BETRIEB.md empfiehlt) alle in der GUI angelegten Streams, alle GUI-Zugänge in `authInternalUsers` und alle Änderungen aus „Erweiterte Konfiguration“. Die GUI stellt nichts davon beim Start oder periodisch wieder her; Encoder schlagen dann bei der Authentifizierung fehl, und je nach Basiskonfiguration gelten wieder die Standardrechte (in der Standard-`mediamtx.yml` darf `any` publizieren). Benötigt: Test mit der eingesetzten MediaMTX-Version. Falls bestätigt: Abgleich (Credentials + Pfade aus der DB) beim Start und periodisch, und Doku anpassen.

**U-2 – Snapshot-Passwort in ffmpeg-Fehlermeldungen.** Die letzte stderr-Zeile wird geloggt und als `title` auf dem Dashboard für alle Benutzer angezeigt (`snapshots.py:100–107`, `stream_card.html:6`). Mit ffmpeg 6.1 enthält die letzte Zeile die URL nicht (getestet: „Error opening input files: Connection refused“), die vorletzte aber schon. Ältere ffmpeg-Versionen (z. B. 5.1 in Debian bookworm) geben im Format `<url>: <fehler>` aus; dann würde das Leserecht-Passwort für alle Pfade an jeden Dashboard-Benutzer gehen. Benötigt: Test im tatsächlich gebauten Image. Unabhängig davon empfehlenswert: URLs mit Zugangsdaten aus Fehlermeldungen herausfiltern.

**U-3 – Rückgabe von Passwörtern durch `/v3/config/global/get`.** Bestimmt, ob F-01 zur Übernahme des API-Zugangs führt oder „nur“ die Konfiguration offenlegt. Indiz dafür: Die Sync-Logik der GUI und die Seite „Erweiterte Konfiguration“ setzen voraus, dass die Werte unverändert zurückkommen.

---

## 4. Positiv aufgefallen

CSRF-Tokens auf allen ändernden Formularen mit `compare_digest`; konstante Laufzeit beim Login gegen Benutzer-Enumeration; Autoescape in allen Templates (kein `|safe`, kein `innerHTML`); strenge CSP ohne Inline-Skripte; `no-store` für alle dynamischen Seiten; Fernet-Verschlüsselung der Zugänge mit Schlüsselvalidierung beim Start; Mindestlänge für `SECRET_KEY`; Docker-Socket doppelt gesperrt; ffmpeg per `create_subprocess_exec` ohne Shell; Admin-Risiko (runOn*-Hooks) offen dokumentiert.

---

## 5. Coverage-Record

**Root-Cause-Facetten (alle 47 an allen Objekten gestellt):**
- Befunde: Vollständigkeit der Verifikation (F-01, F-07, F-08), fehlende Werte/Defaults (F-06, F-16), Woher Vertrauen kommt (F-03), Woran ein Limit gebunden ist (F-03, F-04), Autorität über die Aufgabe hinaus (F-02, F-09), Wirksamkeit deklarierter Kontrollen (F-10), Bindung und Kontext (F-11), mehrere Darstellungen derselben Tatsache (F-12, F-14), Transformation nach Validierung (F-01), Kostenasymmetrie (F-04, F-05), Welcher Kandidat ein Name auflöst (F-07), Lebensdauer referenzierter Namen (F-17, U-1), Wohin Geheimnisse fließen (F-02, F-11, F-18, U-2), Angezeigtes vs. Verwendetes (F-13, F-17), Nebenläufigkeit (F-08), Zustände und Phasen (F-10, U-1), Interpretation von Fehlern/Sonderwerten (F-06, F-14), Rückfallpfade (F-08), Konsistenz paralleler Einstiegspunkte (F-01: nur `new` validiert), Angreiferperspektive/Regressionen (F-19).
- Geprüft, nichts gefunden: Werte, die nicht erratbar sein dürfen (CSRF/`secrets`), beobachtbare Unterschiede (Dummy-Hash), Daten werden zu Instruktionen (kein SQL-String-Bau, kein Shell-Aufruf; JSON-Overrides sind Admin-Funktion), Wer eine Beschränkung lockern darf (nur Admins), Einzigartigkeit/Einmalverwendung (CSRF-Token pro Session – akzeptabel).
- Nicht anwendbar (Begründung: reines Python ohne eigene Binärformate/Puffer/Signaturen/Firmware): Längen-/Offsetberechnung, Parse-Cursor, Datenlayout, Lebensdauer/Ownership von Speicher, gekoppelte Felder, Wiederannahme alter signierter Versionen, Erhaltungsinvarianten, Wahl nach Sehen, Callback-Reentrancy, Typ-/Tag-Konsistenz über Sprachen hinweg, Protokollkonvertierung (nur URL-Bau, unter F-13 erfasst), Eigenschaften, die einen Umzug überleben, Gleichheitsprüfungen, Was eine Prüfung abdeckt, Privilegierter Code mit Low-Privilege-Input (außer ffmpeg, unter F-09), Authentizität von Identitäten (nur interne Dienste), geteilter Zustand über Vertrauensebenen (unter F-12), Reste nach Abbruch (ffmpeg-Timeout räumt `tmp` nicht auf – vernachlässigbar, wird beim nächsten Lauf überschrieben).

**Allgemeine Dimensionen:** Geprüft, Befunde: 8, 9, 10, 12, 13, 14, 18, 19, 20, 23, 27, 28, 29, 30 (Geheimnisse/Personendaten: Benutzernamen, Login-Zeiten), 32, 33, 34, 35, 36, 38, 40–44, 45, 46, 3, 6, 7. Geprüft, nichts gefunden: 1 (kein toter Code außer gesperrter Docker-Steuerung, bewusst), 2 (zwei `noqa`-Unterdrückungen begründet), 4, 5, 11, 15, 16, 17 (UTC durchgängig, nur deprecated API), 22, 24, 31, 37. Nicht anwendbar: 21 (Single-Instance, dokumentiert), 25 (SQLite-Transaktionen, keine eigene Recovery-Logik), 26 (kein Release-/Rollback-Prozess im Repo – nur unter F-19 erfasst), 39 (keine generierten Artefakte).

**Spezialitäten:** Web-Anwendung/Session/CSRF/XSS, Authentifizierung und Passwortspeicherung, HTTP-Client zu internen APIs (SSRF/Pfadinjektion), Subprozesse/Medienverarbeitung, Kryptografie im Ruhezustand, Container/Compose, Reverse-Proxy-Konfiguration, Abhängigkeiten – alle geprüft. Nicht anwendbar: Mobile, Embedded, Blockchain, ML, Desktop, Browser-Erweiterungen u. a.

**Sprachtabellen:** Python (geprüft: Default-Argumente, `assert` in `_loop` – wirkungslos bei `-O`, aber nur Programmierfehlerprüfung; Exception-Verschlucken F-06), JavaScript (DOM nur über `textContent`/Attribute), SQL (ORM, keine Rohabfragen), Konfiguration/YAML/Dockerfile (F-09, F-19), Regex (`PATH_NAME_RE` verankert mit `^…$`; `$` erlaubt abschließendes `\n`, wird aber durch vorheriges `strip()` neutralisiert).

**Ausführungsumgebungen:** Linux-Container (deklariert). Evidenz: statisch für das Image; simulierte Laufzeit (Python 3.13, Ubuntu-ffmpeg 6.1, MediaMTX-Mock) für F-01, F-02, F-10. Keine native Ausführung im echten Image.

## 6. Verifikationsprotokoll

| Schritt | Ergebnis |
|---|---|
| Kopie von `app/` und `requirements.txt` in eine isolierte Sandbox, `venv` mit exakt gepinnten Versionen (Starlette 0.38.6 aufgelöst) | erfolgreich |
| `pip-audit -r requirements.txt` und `pip-audit` auf der Umgebung, 02.11.2026, Datenquelle PyPI/OSV | 24 eindeutige Advisories in 5 Paketen (F-05) |
| Mock-MediaMTX (`127.0.0.1:19997`, liefert fiktive Geheimnisse), App per `uvicorn` auf `127.0.0.1:18080`, `FORCE_SECURE_COOKIES=false`; Skript: Admin-Login → Benutzer `student` (Rolle user) → Zugang `encoder-halle1` (publish) → Snapshot-Konto → Login als `student` → Detailseite und Raw-Socket-Request `GET /streams/..%2F..%2Fglobal%2Fget` | F-01, F-02, F-10 reproduziert (Ausgaben oben) |
| ffmpeg 6.1 gegen geschlossenen Port und gegen RTSP-Server mit 401 | letzte Zeile ohne Zugangsdaten (U-2 offen) |
| Abweichungen zur Produktion | Mock statt MediaMTX, kein TLS/Proxy, Python 3.13 statt 3.12 |
| Aufräumen | Prozesse beendet; Sandbox-Kopie enthält nur fiktive Testdaten; keine Änderungen im Projektordner |

## 7. Änderungen

Keine – das Review war ausschließlich lesend. Alle Befunde sind vorbestehend (Stand 21.10.2026).

## 8. Empfohlene Reihenfolge

1. F-01 und F-02 (Rechtetrennung) sofort beheben; bis dahin keine Konten mit Rolle „Benutzer“ ohne Pfadbeschränkung vergeben und den API-Port 9997 keinesfalls veröffentlichen.
2. U-1 gegen die eingesetzte MediaMTX-Version testen; bei Bestätigung einen Abgleich beim Start einbauen.
3. F-05 (Updates), F-03/F-04 (Login-Härtung), F-09 (Container-Härtung, Port-Bindung).
4. F-06 bis F-08 (Sync-Robustheit), danach die niedrigen Befunde und ein Grundgerüst an Tests (Rollen-/Pfadberechtigung, Traversal, Sync).
