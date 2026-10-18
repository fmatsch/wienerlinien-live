# Wiener Linien Live

[![Tests](https://github.com/fmatsch/wienerlinien-live/actions/workflows/tests.yml/badge.svg)](https://github.com/fmatsch/wienerlinien-live/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/fmatsch/wienerlinien-live?label=Download)](https://github.com/fmatsch/wienerlinien-live/releases/latest)
[![Lizenz: MIT](https://img.shields.io/badge/Lizenz-MIT-blue.svg)](LICENSE)

**Projektseite: [fmatsch.github.io/wienerlinien-live](https://fmatsch.github.io/wienerlinien-live/)** · **Im Browser: [fmatsch.ist/wienerlinien](https://fmatsch.ist/wienerlinien/)**

Berechnete Live-Positionen aller Wiener U-Bahn-Züge (U1, U2, U3, U4, U6) und von 27 Straßenbahnlinien auf einer Karte.
Die Wiener Linien liefern keine GPS-Daten, nur Abfahrtsprognosen pro Haltepunkt. Die Züge werden daraus rekonstruiert.
Privates, inoffizielles Projekt. Die Apps enthalten alles Nötige und brauchen nur eine Internetverbindung.

## Fertige Apps herunterladen

| Plattform | Datei | Update |
|---|---|---|
| macOS (Apple Silicon) | [`Wiener-Linien-Live-mac-arm64.dmg`](https://github.com/fmatsch/wienerlinien-live/releases/latest/download/Wiener-Linien-Live-mac-arm64.dmg) | App meldet neue Version und öffnet den Download |
| macOS (Intel) | [`Wiener-Linien-Live-mac-x64.dmg`](https://github.com/fmatsch/wienerlinien-live/releases/latest/download/Wiener-Linien-Live-mac-x64.dmg) | wie oben |
| Windows | [`Wiener-Linien-Live-win-x64.exe`](https://github.com/fmatsch/wienerlinien-live/releases/latest/download/Wiener-Linien-Live-win-x64.exe) | automatisch im Hintergrund |
| Raspberry Pi / Linux | [`wienerlinien-live-hackberry.tar.gz`](https://github.com/fmatsch/wienerlinien-live/releases/latest/download/wienerlinien-live-hackberry.tar.gz) | lädt, prüft und installiert selbst |

Die Apps sind nicht signiert: macOS beim ersten Start Rechtsklick → Öffnen, Windows SmartScreen „Weitere Informationen → Trotzdem ausführen“.
Meldet macOS stattdessen, die App sei „beschädigt“ (Gatekeeper, die App ist nicht beglaubigt), hilft im Terminal:
`xattr -cr "/Applications/Wiener Linien Live.app"`
Prüfsummen: `SHA256SUMS.txt` in jedem Release.

**Hackberry / Raspberry Pi** (nur Python 3 und Chromium nötig, kein sudo):

```bash
tar xzf wienerlinien-live-hackberry.tar.gz
./wienerlinien-live/install.sh      # legt auch ein Symbol auf dem Desktop an
```

## Update-Schleife

Alle Apps prüfen 10–15 Sekunden nach dem Start und danach alle 6 Stunden die neueste GitHub-Release.
Die Seite zeigt dazu ein Banner (`/_app.json` der lokalen Wrapper-Server).

* **Windows:** `electron-updater` lädt im Hintergrund und installiert beim Beenden (oder per „Neu starten“).
* **macOS:** Squirrel.Mac akzeptiert nur Apps mit bezahltem Apple-Zertifikat, deshalb Hinweis plus Download.
* **Hackberry:** lädt `wienerlinien-live-hackberry.tar.gz`, prüft SHA-256 gegen `SHA256SUMS.txt`, ersetzt sich
  (mit Rollback bei Fehlern) und startet nach „Neu starten“ neu.

## Aufbau

```
web/              Frontend (Leaflet): Karte, Netzplan, Debug-Panel, Update-Banner
backend/          Engine (Positionsberechnung), Poller + Cache + HTTP-API (nur Standardbibliothek)
backend/data/     Netze: network.json (U-Bahn), network_tram.json (Straßenbahn)
app/              Electron-App für macOS und Windows
hackberry/        Starter + Installer für Raspberry Pi / Linux (Chromium-App-Fenster)
scripts/          Datenaufbereitung (GTFS → Netz), Pilot- und Messskripte
deploy/           systemd-Unit und Caddy-Snippet für den API-Server
docs/             GitHub-Pages-Projektseite
```

Die Apps enthalten die Weboberfläche und holen die Daten von der öffentlichen API
(`https://www.depes.ch/wl-api`, Cache-Proxy). Wer selbst hosten will: `make run` startet Backend und Frontend lokal
(`http://127.0.0.1:4731`), `WL_API=https://…` stellt die Apps auf einen eigenen Server um.

## Entwickeln

```bash
make run        # lokal: http://127.0.0.1:4731
make test       # Tests inkl. Replay aufgezeichneter API-Antworten
make record     # Server, der jede rohe API-Antwort nach recordings/ schreibt
make setup      # GTFS-Daten laden und Netze neu bauen (~90 MB Download)

cd app && npm install && npm start     # Desktop-App
```

Release: Tag `vX.Y.Z` pushen, GitHub Actions baut macOS (arm64, x64), Windows und das Hackberry-Paket und veröffentlicht sie.

## Wie die Positionen entstehen

1. `monitor?stopId=…` der Wiener Linien für alle Haltepunkte der Linien, gebündelt (100 IDs pro Request).
2. Pro Linie und Richtung werden Abfahrten benachbarter Stationen per monotonem Matching verkettet
   (Abfahrt in Station *i* + Fahrzeit ≈ Abfahrt in Station *i+1*, Toleranz 110 s). Jede Kette ist ein Zug.
3. Der Kopf der Kette (erste Station, die der Zug noch nicht verlassen hat) bestimmt die Position: in der Station
   (Rest ≤ Haltezeit) oder zwischen zwei Stationen (Anteil aus Restzeit / Fahrzeit).
4. Der Server liefert pro Zug einen Fahrplan aus Wegpunkten; der Browser interpoliert flüssig dazwischen.
5. Identitäten über Polls: Nearest-Neighbour auf der Linienposition. Züge, die an die letzte Station mit Daten
   heranfahren, laufen als „Ghost“ bis zur Ankunft weiter.
6. Fehlen Daten, wird der Zug ausgeblendet statt falsch gezeigt.

Befunde zur API: `vehicle.onStop` ist bei U-Bahn und Straßenbahn nicht befüllt (der Code nutzt es, sobald es erscheint).
Die API antwortet mit `Abfragelimit erreicht` (HTTP 403, Code 316), wenn zu viele Haltepunkte in kurzer Zeit abgefragt
werden (gemessen: ~420 IDs/min dauerhaft ok). Alle Poller teilen sich deshalb einen Token-Bucket (6,5 IDs/s), und die
Straßenbahn fragt nur die gerade eingeblendeten Linien ab.

## Datenquellen und Rechtliches

Stadt Wien – data.wien.gv.at / Wiener Linien (CC BY 4.0), Grundkarte basemap.at (CC BY 4.0).
Kein offizielles Angebot der Wiener Linien; die Positionen sind berechnet und keine verbindliche Auskunft.
