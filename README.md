# MediaMTX GUI

Eine eigenstaendige Web-Oberflaeche fuer einen bestehenden [MediaMTX](https://mediamtx.org)-Server:
Streams mit Vorschaubild anzeigen und konfigurieren, Sende-/Leserechte verwalten, den MediaMTX-Server
steuern, neue Streams anlegen, und das Ganze abgesichert durch eine eigene Benutzerverwaltung mit den
Rollen Admin/Benutzer. Laeuft als separater Docker-Container neben deinem bestehenden MediaMTX-Container.

Technik: Python (FastAPI), serverseitig gerenderte Seiten (kein JS-Build-Prozess noetig), SQLite fuer
die eigenen Daten (Benutzer, Metadaten, Zugangsdaten, Verlauf), ffmpeg fuer die Vorschaubilder.

> Dieses Dokument ist die Kurzfassung (Funktionsueberblick, MediaMTX vorbereiten, Schnellstart). Fuer
> eine vollstaendige Konfigurationsreferenz, Reverse-Proxy-Beispiele, Backup/Restore, Updates, Logging
> und den laufenden Betrieb siehe **[BETRIEB.md](BETRIEB.md)**.

## Funktionen

- **Dashboard**: alle MediaMTX-Streams als Kacheln mit automatisch aktualisiertem Vorschaubild,
  Online/Offline-Status, Anzahl Leser, Tags.
- **Stream-Konfiguration**: Quelle (Publisher / Pull-URL / Weiterleitung / Raspberry-Pi-Kamera),
  Aufnahme-Einstellungen, On-Demand-Verhalten u.v.m. - plus ein Feld fuer beliebige zusaetzliche
  MediaMTX-Parameter als JSON (Hooks, SRT-Passphrasen, WHEP-Einstellungen, ...).
- **Neue Streams anlegen** direkt ueber die Control-API, ohne mediamtx.yml von Hand anzufassen.
- **Sende-/Lese-Zugangsdaten**: eigene Verwaltungsseite fuer MediaMTX-Zugaenge (publish/read/playback),
  wahlweise auf bestimmte Streams beschraenkt, wird automatisch mit der globalen MediaMTX-Konfiguration
  synchronisiert.
- **Verbindungs-URLs** pro Stream zum Kopieren: RTSP, RTMP, HLS, WebRTC (WHIP/WHEP), SRT, sowie
  Wiedergabe-Link fuer Aufnahmen - fertig zum Eintragen in Encoder/Decoder.
- **Server-Steuerung**: Protokolle (RTSP/RTMP/HLS/WebRTC/SRT/Playback) an- und ausschalten, komplette
  globale Konfiguration als JSON bearbeiten, MediaMTX-Docker-Container starten/stoppen/neustarten,
  Live-Verbindungszahlen je Protokoll.
- **Benutzerverwaltung**: GUI-Konten mit Rolle Admin oder Benutzer; Benutzer koennen optional auf
  bestimmte Streams eingeschraenkt werden.
- **Aufnahmen-Browser**: Liste der Aufnahmen je Stream über die MediaMTX-Playback-API, mit Download-Link.
- **Verlauf/Audit-Log**: wer hat wann was geaendert.
- **StudioLink** (optional): Reporter der Field-Reporter-App anlegen/bearbeiten/loeschen, QR-Codes und
  Konfigurationslinks fuer die App erzeugen und anzeigen, Zugaenge widerrufen, Encoder- und Geraetetokens
  ausgeben, Tally/Uplink/App-Verbindung je Reporter und ein Integrationsstatus.
- **MediaMTX-Anmeldung ueber die GUI** (optional, `authMethod: http`): bisherige Zugangsdaten und
  StudioLink-Tokens gleichzeitig.
- **Hell/Dunkel-Design**, responsive.

## Voraussetzungen

- Ein laufender MediaMTX-Container auf demselben Server (bereits vorhanden).
- Docker + Docker Compose auf dem Server.
- Die beiden Container muessen sich im selben Docker-Netzwerk erreichen koennen (siehe
  `docker-compose.example.yml`).

### MediaMTX vorbereiten

Die GUI spricht ausschliesslich mit der MediaMTX **Control-API** (Standardport `9997`) - es wird nichts
direkt in `mediamtx.yml` geschrieben. Zwei Dinge muessen in deiner bestehenden `mediamtx.yml` einmalig
ergaenzt werden:

**1. Control-API aktivieren** (falls noch nicht geschehen):

```yaml
api: yes
apiAddress: :9997
```

**2. Einen Zugang fuer die GUI anlegen.** MediaMTX beschraenkt den API-Zugriff standardmaessig auf
`127.0.0.1` - da die GUI aber aus einem anderen Container kommt, braucht sie einen eigenen Eintrag unter
`authInternalUsers` mit der Berechtigung `api` (und `read`, damit sie fuer die Vorschaubilder Streams per
RTSP lesen kann):

```yaml
authInternalUsers:
  # ... deine bestehenden Eintraege bleiben unveraendert ...

  - user: mediamtx-gui
    pass: EIN_SICHERES_PASSWORT_HIER
    ips: []
    permissions:
      - action: api
      - action: metrics
      - action: read
```

Trage denselben Benutzernamen/Passwort anschliessend in der `.env`-Datei der GUI unter `MTX_API_USER` /
`MTX_API_PASS` ein. Optional, aber empfohlen: `ips` auf den IP-Bereich deines Docker-Netzwerks
beschraenken statt leer zu lassen.

Falls du moechtest, dass Vorschaubilder erzeugt werden und Aufnahmen abrufbar sind, aktiviere zusaetzlich:

```yaml
playback: yes
playbackAddress: :9996
```

**Wichtig:** Sende- und Leserechte fuer einzelne Streams werden **nicht** hier von Hand gepflegt, sondern
komplett ueber die Seite "Zugangsdaten" in der GUI - die GUI schreibt diese automatisch in
`authInternalUsers`, ohne deine obigen manuellen Eintraege anzutasten.

## Installation

```bash
git clone <dieses-repo> mediamtx-gui   # oder die Dateien einfach auf den Server kopieren
cd mediamtx-gui
cp .env.example .env
```

`.env` ausfuellen:

```bash
# Zwei zufaellige Schluessel erzeugen:
openssl rand -hex 32                                                   # -> SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> ENCRYPTION_KEY
```

Danach `MTX_API_URL`, `MTX_API_USER`, `MTX_API_PASS`, `MTX_RTSP_HOST`, `PUBLIC_HOST` und ggf.
`MTX_CONTAINER_NAME` an deine Umgebung anpassen (Kommentare in `.env.example` beachten).

`docker-compose.example.yml` nach `docker-compose.yml` kopieren (oder den Service in deine bestehende
Compose-Datei uebernehmen, siehe Kommentare darin) und anpassen, dann:

```bash
docker compose up -d --build
docker compose logs -f mediamtx-gui
```

Beim allerersten Start wird automatisch ein Admin-Konto angelegt; Benutzername `admin` und ein
zufaelliges Passwort erscheinen **einmalig im Container-Log** (`docker compose logs mediamtx-gui`).
Nach dem ersten Login musst du dieses Passwort sofort aendern.

Die Oberflaeche ist danach unter `http://<server>:8080` erreichbar. Fuer den produktiven Einsatz
empfehlen wir, den Port **nicht** direkt oeffentlich freizugeben, sondern per Reverse-Proxy (z.B. das
nginx/Caddy, das du ohnehin auf deinem Server betreibst) mit TLS zu versehen.

## Erste Schritte

1. Als `admin` einloggen, Passwort aendern.
2. Unter **Server** pruefen, ob die Control-API erreichbar ist (gruener Status). Falls nicht: siehe
   Abschnitt "MediaMTX vorbereiten" oben und Troubleshooting unten.
3. Unter **Server** das "Snapshot-Dienstkonto" einrichten (Button "Jetzt einrichten") - erst danach
   erscheinen Vorschaubilder im Dashboard.
4. Unter **Zugangsdaten** die ersten Sende-/Lese-Zugaenge fuer deine Encoder anlegen.
5. Unter **Neuer Stream** die ersten Streams anlegen, oder bereits in MediaMTX vorhandene Streams
   erscheinen automatisch im Dashboard.
6. Unter **Benutzer** weitere Konten fuer Kolleg*innen/Studierende anlegen (Rolle "Benutzer" fuer
   reinen Lese-/Ansichtszugriff, optional auf bestimmte Streams beschraenkt).

## Sicherheitshinweise

Bereits eingebaut: bcrypt-Passwort-Hashing, signierte HttpOnly-Session-Cookies mit `Secure`-Flag und HSTS
(sobald `FORCE_SECURE_COOKIES=true`, Standard), CSRF-Tokens auf allen aendernden Formularen, Sperre nach 5
Fehlversuchen je Benutzername/IP fuer 15 Minuten, zeitkonstanter Login-Vergleich (kein Username-Enumeration
per Zeitmessung), Mindestlaenge fuer alle manuell vergebenen Passwoerter, verschluesselte Ablage der
Stream-Passwoerter, sowie Sicherheits-Header (CSP ohne `unsafe-inline` fuer Skripte, `X-Frame-Options`,
`nosniff`, kein Caching sensibler Seiten). Trotzdem gilt:

- **TLS ist Pflicht, nicht optional.** Die Anwendung terminiert selbst kein TLS - setze sie hinter einen
  Reverse-Proxy (nginx/Caddy) mit gueltigem Zertifikat, sonst gehen Login und Session-Cookie im Klartext
  ueber das Netz. `FORCE_SECURE_COOKIES=false` nur fuer rein lokale Tests ohne TLS verwenden.
- **Docker-Socket ist gesperrt.** Zugriff auf `/var/run/docker.sock` ist gleichbedeutend mit Root auf dem
  Host (auch mit ":ro"). Deshalb wird der Socket in der Compose-Datei nicht gemountet, und die GUI greift
  zusaetzlich nur darauf zu, wenn `DOCKER_CONTROL_ENABLED=true` gesetzt ist (Standard: `false`). Die
  Start/Stopp/Neustart-Funktion fuer den MediaMTX-Container ist damit deaktiviert; MediaMTX bei Bedarf
  direkt auf dem Server neu starten (`docker restart mediamtx`).
- **Admin-Rolle = volle Kontrolle ueber den MediaMTX-Container.** Das Feld "Erweiterte Parameter (JSON)"
  beim Stream sowie "Erweiterte Konfiguration" auf der Server-Seite erlauben `runOn*`-Hooks, also das
  Ausfuehren beliebiger Befehle mit den Rechten des MediaMTX-Containers. Kombiniert mit aktivierter
  Docker-Steuerung ist ein GUI-Admin-Konto damit faktisch gleichbedeutend mit Root auf dem Server - Admin-
  Konten entsprechend sparsam vergeben.
- Stream-Passwoerter werden in der SQLite-Datenbank verschluesselt abgelegt (`ENCRYPTION_KEY`), nicht im
  Klartext - trotzdem `.env` und die Datenbankdatei (`data/mediamtx_gui.db`) wie andere Zugangsdaten
  behandeln (Dateirechte einschraenken, ins Backup nur verschluesselt aufnehmen).
- Die Login-Sperre ist bewusst einfach gehalten (prozessintern, kein Redis o.ae.) und setzt sich bei
  einem Neustart des Containers zurueck. Fuer eine einzelne Instanz (so wie hier vorgesehen) ausreichend;
  bei mehreren Replikas hinter einem Load Balancer muesste das durch einen geteilten Speicher ersetzt
  werden.
- Es gibt keine Zwei-Faktor-Authentifizierung. Bei besonders sensiblen Setups einen Reverse-Proxy mit
  zusaetzlicher Authentifizierung (z.B. per SSO/Basic-Auth-Vorschaltung) in Erwaegung ziehen.

## StudioLink (Field-Reporter-App)

Die GUI kann das [StudioLink](https://github.com/raindropmedia/Studio-link)-Backend verwalten. Die
Schluessel und die Reporterliste bleiben im Backend; die GUI ruft nur dessen Admin-API auf.

### 1. GUI mit dem Backend verbinden (`.env`)

```
STUDIOLINK_API_URL=http://host.docker.internal:8787   # Backend nativ auf dem Host
STUDIOLINK_ADMIN_TOKEN=<adminToken aus config.json des Backends>
MTX_AUTH_SECRET=<openssl rand -hex 24>
```

Laeuft das Backend auf dem Host und die GUI in Docker, muss das Backend auf einer fuer den Container
erreichbaren Adresse lauschen (`"http": {"host": "0.0.0.0"}` bzw. die Docker-Bridge-IP) - Port 8787 dabei
nicht in der Firewall nach aussen oeffnen. Danach erscheint der Menuepunkt **StudioLink**.

### 2. MediaMTX auf die GUI als Anmeldestelle umstellen

MediaMTX kennt nur *eine* Anmeldemethode. `internal` kennt keine Tokens, `jwt` keine Benutzer/Passwoerter.
Damit beides gleichzeitig funktioniert, fragt MediaMTX bei jeder Verbindung die GUI:

```yaml
authMethod: http
authHTTPAddress: http://127.0.0.1:8080/mtx/auth?secret=<MTX_AUTH_SECRET>
authHTTPExclude: []
```

(`127.0.0.1:8080`, wenn MediaMTX nativ auf dem Host laeuft und die GUI ihren Port 8080 veroeffentlicht;
im gemeinsamen Docker-Netz `http://mediamtx-gui:8080/...`.)

Die GUI entscheidet dann so:

- **authInternalUsers** aus der MediaMTX-Konfiguration werden mit denselben Regeln wie von MediaMTX
  ausgewertet (Benutzer `any`, Passwort im Klartext oder `sha256:`, IP-Bereiche, Pfade inkl. `~Regex`).
  Alle Zugaenge unter "Zugangsdaten" und von Hand gepflegte Eintraege gelten also unveraendert weiter.
- **StudioLink-Tokens** (SRT-streamid `…:jwt:<token>`, RTSP/RTMP `?jwt=`, Bearer) werden mit dem
  oeffentlichen Schluessel des Backends (JWKS) geprueft; widerrufene oder geloeschte Reporter werden
  abgelehnt, Geraetetokens nicht.
- **API-Zugriffe** der GUI selbst (`MTX_API_USER`/`MTX_API_PASS`) werden direkt erlaubt.

Wichtig:

- Laeuft die GUI nicht, lehnt MediaMTX **alle** Anmeldungen ab. Die GUI daher mit
  `restart: unless-stopped` betreiben (Standard in `docker-compose.yml`).
- Einen `authInternalUsers`-Eintrag `user: any` mit `publish` auf alle Pfade entfernen - sonst darf jeder
  ohne Token senden.
- Der Eintrag `user: any` mit `ips: [127.0.0.1, ::1]` und `action: api` muss bleiben, damit das Backend
  den Uplink-Status abfragen kann.
- `/mtx/auth` nicht ueber den oeffentlichen Reverse-Proxy freigeben (Beispiele in `reverse-proxy/`
  sperren den Pfad). Zugriffe darauf erscheinen nicht im Zugriffsprotokoll, weil die URL das Secret
  enthaelt.
- Die zuletzt abgelehnten Anmeldungen (mit Grund) zeigt die Seite **StudioLink**.

### 3. Bedienung

**StudioLink → Neuen Reporter anlegen** (ID, Name, optional TSL-Tally, SRT-Passphrase, Videoprofil),
danach **QR-Code** erzeugen (verwaltet oder standalone, Gueltigkeit in Stunden). Der QR-Code wird angezeigt
(Klick vergroessert ihn), der Link laesst sich kopieren und z.B. per AirDrop oder Messenger verschicken.
**Widerrufen** macht alle bisherigen Codes eines Reporters sofort ungueltig und trennt die App.
**StudioLink → Geraetetokens**: langlebige Zugaenge fuer Studiogeraete (Ultra Stream, ZowieBox) anlegen
(mit Vorlagen je Reporter), in einer Liste mit Ablaufdatum verwalten, die fertigen SRT/RTMP/RTSP-URLs
jederzeit wieder anzeigen und Tokens loeschen. Ein geloeschtes Token wird bei der naechsten Anmeldung
abgelehnt; eine bereits laufende Verbindung bleibt bis zum Neuaufbau bestehen.

Der **Integrationsstatus** prueft Backend-Verbindung, SRT-Adresse fuer die App, Steuerkanal-URL,
geladenen Schluessel, MediaMTX-Anmeldemethode, ob MediaMTX den Endpunkt tatsaechlich abfragt, und ob
Pfade fuer `field/<id>/pgm` und `studio/<id>/ret` konfiguriert sind.

## Moegliche Erweiterungen (nicht enthalten)

Bewusst nicht umgesetzt, um den Umfang uebersichtlich zu halten - bei Bedarf gerne nachruesten:
Benachrichtigungen (E-Mail/Webhook) bei Stream-Ausfall, QR-Codes fuer mobile Player-Links (fuer die
StudioLink-App vorhanden),
Massenaktionen (z.B. Aufnahme fuer alle Streams gleichzeitig umschalten), feingranulare UI fuer
Raspberry-Pi-Kamera-Parameter (aktuell nur ueber das JSON-Feld erreichbar).

## Troubleshooting

**"Control-API nicht erreichbar" auf der Server-Seite**
Pruefe: `api: yes` in mediamtx.yml gesetzt? `MTX_API_URL` zeigt auf den richtigen Hostnamen/Port
(Container-Name von MediaMTX im gemeinsamen Docker-Netzwerk, Port 9997)? Beide Container im selben
Docker-Netzwerk? `MTX_API_USER`/`MTX_API_PASS` stimmen mit einem `authInternalUsers`-Eintrag mit
`action: api` in mediamtx.yml ueberein?

**Keine Vorschaubilder**
Snapshot-Dienstkonto unter "Server" eingerichtet? `MTX_RTSP_HOST`/`MTX_RTSP_PORT` korrekt? Ist der Stream
ueberhaupt online (Publisher sendet gerade)? ffmpeg-Fehlermeldungen erscheinen im Container-Log.

**Docker-Steuerung zeigt "gesperrt"**
Das ist so gewollt - die Container-Steuerung ist aus Sicherheitsgruenden deaktiviert (siehe
Sicherheitshinweise). MediaMTX direkt auf dem Server mit `docker restart mediamtx` neu starten.

**Nach dem Loeschen eines Zugangs ist er in MediaMTX trotzdem noch aktiv**
Die Synchronisation laeuft automatisch bei jeder Aenderung ueber die GUI. Falls sie einmal fehlschlaegt
(Fehlermeldung wird angezeigt), einfach auf der Zugangsdaten-Seite eine beliebige Kleinigkeit erneut
speichern, um die Synchronisation zu wiederholen.

**StudioLink: App bekommt keine Verbindung (Uplink/Return rot)**
Seite **StudioLink** oeffnen: Der Integrationsstatus zeigt, was fehlt, und unten stehen die zuletzt
abgelehnten Anmeldungen mit Grund. Haeufig: `server.host` im Backend ist `127.0.0.1`, MediaMTX steht noch
auf `authMethod: internal`, UDP 8890 ist in der Firewall zu, oder der Return-Pfad `studio/<id>/ret` hat
noch keinen Sender (dann ist Return-Rot normal).

**StudioLink: "Admin-Token abgelehnt"**
`STUDIOLINK_ADMIN_TOKEN` stimmt nicht mit `adminToken` bzw. `STUDIOLINK_ADMIN_TOKEN` des Backends ueberein.
