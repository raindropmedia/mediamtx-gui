# Installations- und Betriebsanleitung

Diese Anleitung ergaenzt die Kurzfassung in [README.md](README.md) (Funktionsueberblick, MediaMTX
vorbereiten, Schnellstart) um alles, was fuer einen laengerfristigen, produktiven Betrieb dazugehoert:
vollstaendige Konfigurationsreferenz, Reverse-Proxy-Einrichtung, Tagesbetrieb, Backup/Restore, Updates
und eine Port-/Firewall-Uebersicht.

## Inhalt

1. [Voraussetzungen](#1-voraussetzungen)
2. [Installation Schritt fuer Schritt](#2-installation-schritt-fuer-schritt)
3. [Vollstaendige Konfigurationsreferenz (.env)](#3-vollstaendige-konfigurationsreferenz-env)
4. [Reverse-Proxy / TLS einrichten](#4-reverse-proxy--tls-einrichten)
5. [Port- und Firewall-Uebersicht](#5-port--und-firewall-uebersicht)
6. [Erstinbetriebnahme](#6-erstinbetriebnahme)
7. [Laufender Betrieb (Tagesgeschaeft)](#7-laufender-betrieb-tagesgeschaeft)
8. [Logging & Monitoring](#8-logging--monitoring)
9. [Backup & Wiederherstellung](#9-backup--wiederherstellung)
10. [Updates / Upgrades](#10-updates--upgrades)
11. [Neustart, Stopp, Deinstallation](#11-neustart-stopp-deinstallation)
12. [Grenzen des aktuellen Standes](#12-grenzen-des-aktuellen-standes)

---

## 1. Voraussetzungen

- Linux-Server mit Docker Engine (>= 24) und Docker Compose Plugin (`docker compose version`
  funktioniert).
- Ein bereits laufender MediaMTX-Container auf demselben Server oder im selben Docker-Netzwerk.
- Ein Domainname (empfohlen) fuer den TLS-Zugriff auf die GUI, oder zumindest eine feste IP.
- Ausreichend Speicherplatz fuer Aufnahmen/Vorschaubilder im gemounteten `data`-Volume (Vorschaubilder
  sind klein, ca. 20-150 KB je Stream und Aktualisierung; sie werden bei jedem Snapshot ueberschrieben,
  wachsen also nicht unbegrenzt).
- ffmpeg wird NICHT separat auf dem Host benoetigt - es ist im Docker-Image der GUI bereits enthalten.

## 2. Installation Schritt fuer Schritt

```bash
# 1. Dateien auf den Server bringen
git clone <dieses-repo> mediamtx-gui   # oder: Zip entpacken / scp
cd mediamtx-gui

# 2. Konfiguration vorbereiten
cp .env.example .env
openssl rand -hex 32
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# beide erzeugten Werte in .env eintragen (SECRET_KEY / ENCRYPTION_KEY)
nano .env   # restliche Werte ausfuellen, siehe Abschnitt 3

# 3. MediaMTX vorbereiten (einmalig) - siehe README.md, Abschnitt "MediaMTX vorbereiten"
#    (Control-API aktivieren + authInternalUsers-Eintrag fuer die GUI ergaenzen)
nano /pfad/zu/mediamtx.yml

# 4. Compose-Datei einrichten
cp docker-compose.example.yml docker-compose.yml
nano docker-compose.yml   # Netzwerk an dein bestehendes MediaMTX-Setup anpassen

# 5. Bauen und starten
docker compose up -d --build
docker compose logs -f mediamtx-gui
```

Beim allerersten Start werden automatisch angelegt:
- das SQLite-Datenverzeichnis unter `/data` (im Volume `mediamtx-gui-data`),
- ein Admin-Konto `admin` mit zufaelligem Passwort, das **einmalig im Log** ausgegeben wird.

Sichtbares Erfolgskriterium: `docker compose logs mediamtx-gui` zeigt die Zeile "Erstes Admin-Konto
angelegt" mit Benutzername und Passwort, danach laeuft der Uvicorn-Server auf Port 8080.

## 3. Vollstaendige Konfigurationsreferenz (.env)

| Variable | Pflicht | Standard | Bedeutung |
|---|---|---|---|
| `SECRET_KEY` | **ja** | - | Signiert die Session-Cookies. Mind. 32 Zeichen, sonst startet die Anwendung nicht. Erzeugen: `openssl rand -hex 32`. Bei Aenderung werden alle bestehenden Sitzungen ungueltig. |
| `ENCRYPTION_KEY` | **ja** | - | Verschluesselt Stream-Passwoerter in der Datenbank. Muss ein gueltiger Fernet-Schluessel sein, sonst startet die Anwendung nicht. Erzeugen: siehe Abschnitt 2. **Bei Verlust dieses Schluessels sind alle gespeicherten Stream-Passwoerter unwiederbringlich** - Zugangsdaten muessten neu angelegt werden. |
| `MTX_API_URL` | nein | `http://mediamtx:9997` | Basis-URL der MediaMTX Control-API, aus Sicht des GUI-Containers (i.d.R. Containername + Port 9997). |
| `MTX_API_USER` / `MTX_API_PASS` | nein | leer | Zugangsdaten fuer die Control-API, siehe README "MediaMTX vorbereiten". Ohne diese kann die GUI MediaMTX i.d.R. nicht erreichen. |
| `MTX_RTSP_HOST` | nein | `mediamtx` | Hostname von MediaMTX aus GUI-Sicht, fuer die RTSP-Vorschaubild-Pulls per ffmpeg. |
| `MTX_RTSP_PORT` | nein | `8554` | RTSP-Port von MediaMTX. |
| `MTX_PLAYBACK_URL` | nein | leer | Basis-URL des MediaMTX-Playback-Servers (Port 9996), fuer den Aufnahmen-Browser. Leer lassen, wenn `playback: yes` in mediamtx.yml nicht gesetzt ist. |
| `PUBLIC_HOST` | nein | `localhost` | Der von aussen erreichbare Hostname/die IP, mit der die angezeigten RTSP/RTMP/HLS/WebRTC/SRT-Verbindungs-URLs gebaut werden. **Unbedingt auf die tatsaechlich erreichbare Adresse setzen**, sonst funktionieren die angezeigten Kopier-Links fuer Encoder/Decoder nicht. |
| `DOCKER_CONTROL_ENABLED` | nein | `false` | Sperre fuer die Docker-Container-Steuerung. Solange `false`, greift die GUI nie auf den Docker-Socket zu. Der Socket wird in der Compose-Datei ohnehin nicht gemountet (siehe Sicherheitshinweise im README). |
| `MTX_CONTAINER_NAME` | nein | leer | Name des MediaMTX-Docker-Containers. Wird nur bei `DOCKER_CONTROL_ENABLED=true` und gemountetem Socket verwendet; im Standardbetrieb leer lassen. |
| `SNAPSHOT_INTERVAL_SECONDS` | nein | `15` | Wie oft ein neues Vorschaubild je aktivem Stream gezogen wird. Kleinere Werte = aktuellere Bilder, aber mehr CPU-/Netzlast bei vielen Streams. |
| `SESSION_MAX_AGE_MINUTES` | nein | `480` (8h) | Nach dieser Zeit muss sich ein Benutzer erneut anmelden. |
| `FORCE_SECURE_COOKIES` | nein | `true` | Erzwingt das Secure-Flag am Session-Cookie + HSTS. **Nur fuer lokale Tests ohne TLS auf `false` setzen**, siehe Sicherheitshinweise. |

Nicht in `.env` konfigurierbar, aber gut zu wissen: das Datenverzeichnis liegt im Container immer unter
`/data` (Umgebungsvariable `MEDIAMTX_GUI_DATA_DIR`, im mitgelieferten Dockerfile fest gesetzt) und wird
per Docker-Volume (`mediamtx-gui-data`) persistiert.

### StudioLink und MediaMTX-Anmeldung

| Variable | Bedeutung |
| --- | --- |
| `STUDIOLINK_API_URL` | Admin-API des StudioLink-Backends aus Sicht der GUI, z.B. `http://host.docker.internal:8787`. Leer = StudioLink ausgeblendet. |
| `STUDIOLINK_ADMIN_TOKEN` | `adminToken` des Backends. |
| `STUDIOLINK_JWKS_URL` | optional, Standard `<STUDIOLINK_API_URL>/.well-known/jwks.json`. Der zuletzt geladene Schluessel wird in `/data/studiolink-jwks.json` zwischengespeichert. |
| `MTX_AUTH_SECRET` | aktiviert `/mtx/auth` fuer `authMethod: http` in MediaMTX. |
| `MTX_JWT_CLAIM_KEY` | Claim mit den Berechtigungen (Standard `mediamtx_permissions`). |
| `MTX_AUTH_REFRESH_SECONDS` | wie oft Benutzerliste, Reporter und Schluessel neu geladen werden (Standard 15). |
| `DISPLAY_TIMEZONE` | Zeitzone fuer angezeigte Zeitpunkte (Standard `Europe/Berlin`). |

Siehe README, Abschnitt "StudioLink", fuer die Umstellung von MediaMTX auf `authMethod: http`.

## 4. Reverse-Proxy / TLS einrichten

Die Anwendung selbst spricht nur unverschluesseltes HTTP auf Port 8080 - **TLS muss durch einen
vorgeschalteten Reverse-Proxy erfolgen**, sonst gehen Login-Daten und Session-Cookie im Klartext ueber
das Netz (siehe README, Abschnitt "Sicherheitshinweise").

Fertige Beispielkonfigurationen liegen unter [`reverse-proxy/`](reverse-proxy/):

- [`reverse-proxy/Caddyfile.example`](reverse-proxy/Caddyfile.example) - Caddy holt/erneuert das
  Let's-Encrypt-Zertifikat automatisch, am wenigsten Konfigurationsaufwand.
- [`reverse-proxy/nginx.example.conf`](reverse-proxy/nginx.example.conf) - nginx mit certbot-Zertifikat,
  falls du (wie auf deinem Hestia-/Plesk-Server ueblich) bereits nginx im Einsatz hast.

In beiden Faellen: Domain anpassen, `proxy_pass`/`reverse_proxy`-Ziel auf `mediamtx-gui:8080` (im
selben Docker-Netzwerk) oder `127.0.0.1:8080` (falls der Proxy direkt auf dem Host laeuft und der
GUI-Port nur auf `127.0.0.1` veroeffentlicht wird) setzen. Danach den GUI-Port in der
`docker-compose.yml` **nicht mehr oeffentlich** binden, sondern z.B.
`127.0.0.1:8080:8080` statt `8080:8080`, damit die Anwendung ausschliesslich ueber den Proxy erreichbar
ist.

## 5. Port- und Firewall-Uebersicht

| Port | Von wem erreichbar | Zweck |
|---|---|---|
| GUI 8080 (HTTP) | Nur `127.0.0.1` bzw. der Reverse-Proxy | Web-Oberflaeche - niemals direkt oeffentlich freigeben |
| Reverse-Proxy 443 (HTTPS) | Oeffentlich | Eigentlicher Zugriffspunkt auf die GUI |
| MediaMTX API 9997 | Nur intern, GUI-Container | Control-API, sollte nicht oeffentlich erreichbar sein |
| MediaMTX RTSP 8554, RTMP 1935, HLS 8888, WebRTC 8889 (+8189/udp), SRT 8890/udp | Oeffentlich (bzw. so weit wie deine Encoder/Player reichen muessen) | Eigentliche Stream-Ein-/Ausgaenge - unveraendert wie bisher, die GUI aendert daran nichts |
| MediaMTX Playback 9996 | Optional oeffentlich, falls Aufnahmen-Download extern gebraucht wird, sonst nur intern | Aufnahmen abrufen |

## 6. Erstinbetriebnahme

1. Mit `admin` und dem im Log ausgegebenen Passwort einloggen, sofort Passwort aendern (wird beim
   ersten Login erzwungen).
2. Unter **Server** pruefen: Control-API-Status gruen? Falls nicht, README-Abschnitt "MediaMTX
   vorbereiten" und Troubleshooting pruefen.
3. Dort auch das **Snapshot-Dienstkonto einrichten** (Button "Jetzt einrichten") - erst danach liefert
   das Dashboard Vorschaubilder.
4. Unter **Zugangsdaten** die ersten Sende-/Lese-Zugaenge fuer deine Encoder/Decoder anlegen (Passwort
   wird einmalig angezeigt - sofort sichern, es wird nicht erneut angezeigt).
5. Unter **Neuer Stream** eigene Streams anlegen; bereits in MediaMTX vorhandene Streams erscheinen
   automatisch im Dashboard, ohne dass du sie in der GUI neu anlegen musst.
6. Unter **Benutzer** weitere Konten fuer Kolleg*innen/Studierende anlegen. Rolle "Benutzer" fuer
   reinen Ansichtszugriff, optional beschraenkt auf bestimmte Streams (Feld "Sichtbare Streams").
7. `PUBLIC_HOST` in `.env` kontrollieren - nur damit stimmen die im Dashboard angezeigten
   Verbindungs-URLs fuer Encoder/Decoder.

## 7. Laufender Betrieb (Tagesgeschaeft)

**Neue Kolleg*in/Student*in Zugriff geben**: Benutzer → Neuer Benutzer → Rolle waehlen → ggf. auf
bestimmte Streams beschraenken. Das angezeigte Initialpasswort weitergeben; beim ersten Login wird eine
Aenderung erzwungen.

**Passwort vergessen**: Benutzer → betroffenes Konto → Bearbeiten → Haken bei "Passwort zuruecksetzen" →
Speichern. Neues Passwort wird einmalig angezeigt.

**Encoder-Zugang rotieren** (z.B. nach Verdacht auf Kompromittierung): Zugangsdaten → betroffenen Zugang
→ Bearbeiten → neues Passwort eintragen → Speichern (wird automatisch mit MediaMTX synchronisiert, alte
aktive Verbindungen mit dem alten Passwort werden dadurch NICHT sofort getrennt, sondern erst bei der
naechsten Neuverbindung abgelehnt).

**Stream vorruebergehend sperren**: entweder den zugehoerigen Zugang unter Zugangsdaten loeschen/anpassen,
oder den Stream selbst loeschen (Stream-Konfiguration bleibt als Kopie im Aenderungsverlauf nachvollziehbar,
aber nicht wiederherstellbar - vor dem Loeschen ggf. die JSON-Konfiguration auf der Detailseite notieren).

**Serverweite Aenderungen** (z.B. ein Protokoll global abschalten): Server → Protokolle, oder fuer alles,
was dort nicht als Schalter existiert, "Erweiterte Konfiguration" (rohes JSON, nur fuer Admins mit
MediaMTX-Kenntnissen - siehe Warnhinweis auf der Seite).

**MediaMTX neu laden/neustarten**: Konfigurationsaenderungen ueber die GUI wirken sofort (Hot-Reload
ueber die API, kein Neustart noetig). Ein echter Container-Neustart ist nur noetig, wenn z.B. das
MediaMTX-Docker-Image selbst aktualisiert wurde - dafuer direkt auf dem Server `docker restart mediamtx`
ausfuehren (die Container-Steuerung in der GUI ist gesperrt, kein Zugriff auf den Docker-Socket).

## 8. Logging & Monitoring

- **Logs ansehen**: `docker compose logs -f mediamtx-gui` (laufend) bzw. `docker compose logs --tail 200
  mediamtx-gui`. Enthaelt u.a. die ffmpeg-Fehlermeldungen bei fehlgeschlagenen Snapshots und alle vom
  Docker-Log-Treiber erfassten Anwendungs-Logs.
- **Log-Rotation**: wird nicht von der Anwendung selbst uebernommen, sondern vom Docker-Log-Treiber.
  Falls noch nicht global konfiguriert, in der `docker-compose.yml` ergaenzen:
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
- **Healthcheck**: Der Container bringt einen eingebauten Docker-Healthcheck mit (`GET /healthz` alle
  30s, siehe `docker ps` Spalte STATUS bzw. `docker inspect --format='{{json .State.Health}}'
  mediamtx-gui`). Fuer externes Monitoring (z.B. Uptime-Kuma, Healthchecks.io o.ae.) denselben Endpunkt
  ueber den Reverse-Proxy abfragen: `GET https://<domain>/healthz` (liefert `{"status":"ok"}`, erfordert
  keinen Login).
- **Aenderungsverlauf in der GUI selbst**: Menuepunkt "Verlauf" zeigt die letzten 200 Aktionen (Login,
  Stream-/Konfigurationsaenderungen, Benutzerverwaltung) mit Zeitstempel und ausfuehrendem Benutzer -
  guter erster Anlaufpunkt bei der Frage "wer hat das geaendert".

## 9. Backup & Wiederherstellung

Zu sichern sind zwei Dinge:

1. **Das Datenverzeichnis** (`/data` im Container, Docker-Volume `mediamtx-gui-data`): enthaelt die
   SQLite-Datenbank (Benutzer, Metadaten, verschluesselte Stream-Passwoerter, Verlauf) und die
   Vorschaubilder. Die Vorschaubilder muessen nicht gesichert werden (werden automatisch neu erzeugt),
   die Datenbank unbedingt.
2. **Die `.env`-Datei**: insbesondere `ENCRYPTION_KEY` - ohne diesen Schluessel sind die in der
   Datenbank gesicherten Stream-Passwoerter beim Wiederherstellen nicht mehr entschluesselbar.

Backup-Kommando (konsistentes Backup waehrend die Anwendung laeuft - SQLite handhabt das unkritisch,
bei sehr hoher Schreiblast waere ein kurzer Stop sauberer):

```bash
docker run --rm -v mediamtx-gui_mediamtx-gui-data:/data -v "$PWD":/backup \
  alpine tar czf /backup/mediamtx-gui-data-$(date +%Y%m%d-%H%M).tar.gz -C /data .
cp .env mediamtx-gui-env-backup-$(date +%Y%m%d).env   # getrennt und besonders geschuetzt aufbewahren
```

(Volume-Name ggf. mit `docker volume ls` pruefen - er setzt sich aus Compose-Projektname und dem in der
`docker-compose.yml` definierten Volume-Namen zusammen.)

Wiederherstellung:

```bash
docker compose down
docker run --rm -v mediamtx-gui_mediamtx-gui-data:/data -v "$PWD":/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/mediamtx-gui-data-JJJJMMTT-HHMM.tar.gz -C /data"
# .env aus dem Backup wiederherstellen (gleicher ENCRYPTION_KEY!)
docker compose up -d
```

Falls du auf diesem Server bereits eine Backup-Loesung fuer andere Dienste einsetzt (z.B. Borg, restic
oder ein einfaches Cron-Skript mit `tar`), das Datenverzeichnis und die `.env`-Datei dort mit
aufnehmen, statt eine zweite, separate Backup-Routine zu pflegen.

## 10. Updates / Upgrades

```bash
git pull   # oder: neue Dateien ueber die alten kopieren
docker compose build --no-cache mediamtx-gui
docker compose up -d
docker compose logs -f mediamtx-gui   # pruefen, dass der Start sauber durchlaeuft
```

Kurze Downtime der GUI waehrend des Neustarts ist zu erwarten (MediaMTX selbst und laufende Streams sind
davon nicht betroffen, die GUI ist nur die Verwaltungsoberflaeche). Datenbankschema-Aenderungen: die
Anwendung legt neue Tabellen beim Start automatisch an (`Base.metadata.create_all`), es gibt aktuell
aber **kein Migrationswerkzeug** fuer Aenderungen an bestehenden Tabellen/Spalten - ein Backup vor jedem
Update (Abschnitt 9) ist daher empfehlenswert.

## 11. Neustart, Stopp, Deinstallation

```bash
docker compose restart mediamtx-gui   # GUI neu starten, MediaMTX unberuehrt
docker compose stop mediamtx-gui      # GUI anhalten
docker compose start mediamtx-gui     # wieder starten
docker compose down                   # Container entfernen, Daten-Volume bleibt erhalten
docker compose down -v                # ACHTUNG: entfernt auch das Daten-Volume (alle Benutzer,
                                       # Zugangsdaten, Verlauf) - vorher Backup!
```

Ein Entfernen der GUI (auch mit `-v`) hat keinerlei Auswirkung auf den MediaMTX-Container oder dessen
Konfiguration - die GUI schreibt Sende-/Leserechte zwar in MediaMTX' `authInternalUsers`, entfernt diese
beim eigenen Ausbau aber nicht automatisch wieder (das muesste danach von Hand in `mediamtx.yml`
bereinigt werden, falls gewuenscht).

## 12. Grenzen des aktuellen Standes

- Single-Instance-Design: kein Cluster-/Mehrfach-Container-Betrieb vorgesehen (Rate-Limiting und
  Snapshot-Hintergrundjob sind prozesslokal).
- Kein Migrationswerkzeug fuer Datenbankschema-Aenderungen (siehe Abschnitt 10).
- Keine Zwei-Faktor-Authentifizierung.
- Keine automatischen Benachrichtigungen bei Stream-Ausfall (siehe README, "Moegliche Erweiterungen").
