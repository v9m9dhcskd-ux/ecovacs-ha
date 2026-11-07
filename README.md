# Ecovacs Omni für Home Assistant

Eigene Home-Assistant-Integration zur vollständigen Steuerung von Ecovacs-Deebot-Robotern,
entwickelt für den **DEEBOT T50 MAX PRO OMNI**. Sie funktioniert auch mit anderen Modellen,
die die Bibliothek [`deebot-client`](https://github.com/DeebotUniverse/client.py) kennt.

Der Schwerpunkt liegt auf dem, was der offiziellen Ecovacs-Integration fehlt:

- **Einzelne oder mehrere Räume reinigen, in einer festen Reihenfolge**
  - Raumauswahl per Schalter im Dashboard
  - Reinigung per Aktion `ecovacs_omni.clean_rooms`
- **Ganze Wohnung reinigen** mit einem Knopf
- **Station steuern:** Staub absaugen, Mopps waschen, Mopps trocknen, Station reinigen
- **Alle wichtigen Status**
  - Zustand, Akku, Fehler
  - Fläche und Dauer der aktuellen Reinigung, letzter Auftrag
  - Zustand der Station
  - Verbrauchsteile, Gesamtstatistik
  - Live-Karte

Die Verbindung läuft über die Ecovacs-Cloud, also mit demselben Konto wie die Ecovacs-Home-App.
Statusänderungen kommen per MQTT sofort an, Home Assistant fragt nichts im Takt ab.

> Voraussetzung: **Home Assistant 2026.11** oder neuer.

## Installation über HACS

1. HACS → ⋮ → **Benutzerdefinierte Repositories**
2. URL `https://github.com/v9m9dhcskd-ux/ecovacs-ha`, Typ **Integration**
3. **Ecovacs Omni** installieren und Home Assistant neu starten
4. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Ecovacs Omni*
5. E-Mail, Passwort und Land des Ecovacs-Kontos eingeben
   - Wenn Ecovacs einen **Bestätigungscode** per E-Mail schickt, wird er im nächsten Schritt abgefragt.

Die offizielle Ecovacs-Integration kann parallel weiterlaufen. Wenn alles passt, kannst du sie entfernen.

## Entitäten

Die Entitäts-IDs richten sich nach dem Namen des Roboters (Beispiel unten: `robbi`) und nach der
Sprache von Home Assistant.

| Typ | Entitäten |
|---|---|
| Staubsauger | `vacuum.robbi`: Start/Fortsetzen, Pause, Stopp, zur Station, Saugstärke, Orten (Ton) |
| Buttons | Ganze Wohnung reinigen, Ausgewählte Räume reinigen, Raumauswahl leeren, Raum-Warteschlange abbrechen, Staub absaugen, Mopps waschen, Mopps trocknen, Station reinigen, Neu orten, Verbrauchsteile zurücksetzen (standardmäßig deaktiviert) |
| Schalter | **Raum ‹Name›** (ein Schalter pro Raum), **Räume nacheinander reinigen**, Automatisch absaugen, Teppich-Boost, Nach dem Laden fortsetzen, Kindersicherung, TrueDetect, … |
| Auswahl | Reinigungsmodus (Saugen/Wischen/beides/erst Saugen dann Wischen), Absaug-Häufigkeit, Effizienzmodus, Aktive Karte |
| Zahlen | Wassermenge (0–50), Durchgänge, Lautstärke |
| Sensoren | Batterie, Gereinigte Fläche, Reinigungsdauer, Letzter Auftrag, Station, Fehler, **Ausgewählte Räume**, **Raum-Warteschlange**, Verbrauchsteile in %, Gesamtstatistik, WLAN (standardmäßig deaktiviert) |
| Binärsensoren | Mopp montiert, In der Station |
| Bild | Karte |

## Räume reinigen

Die Räume werden automatisch aus der im Roboter gespeicherten Karte gelesen. Benutzt werden die
Namen, die du in der Ecovacs-App vergeben hast. Benennst du dort Räume um oder teilst sie neu auf,
erscheinen die Änderungen von selbst.

### Im Dashboard

1. Die gewünschten Räume über die Schalter **Raum ‹Name›** einschalten. **Die Reihenfolge des
   Einschaltens ist die Reinigungsreihenfolge.** Der Sensor *Ausgewählte Räume* zeigt sie an,
   z. B. `Küche → Flur → Wohnzimmer`.
2. Den Button **Ausgewählte Räume reinigen** drücken.

Die Auswahl bleibt über Neustarts erhalten. Startest du eine Reinigung auf anderem Weg
(„Reinigung nach Bereich“ im Staubsauger-Dialog, `vacuum.clean_area` oder
`ecovacs_omni.clean_rooms`), übernehmen *Ausgewählte Räume* und die Raum-Schalter deren Räume. Ein fertiges Dashboard-Beispiel liegt in
[`examples/dashboard.yaml`](examples/dashboard.yaml).

### Reihenfolge: „Räume nacheinander reinigen“

| Schalter | Verhalten |
|---|---|
| **An** (Standard) | Die Integration schickt einen Raum, wartet, bis der Roboter ihn als fertig meldet, und schickt dann den nächsten. **Die Reihenfolge ist garantiert.** Der Sensor *Raum-Warteschlange* zeigt den aktuellen Raum und die restlichen Räume. |
| **Aus** | Alle Räume gehen in einem Befehl an den Roboter, wie bei der Ecovacs-App. Das ist schneller und ohne Zwischenstopps. Neuere Modelle wie der T50 halten nach Berichten aus der Community auch hier die Reihenfolge ein. Bitte selbst ausprobieren. |

Verhalten der Warteschlange:

- **Pause** hält auch die Warteschlange an. **Start** setzt beide fort.
- **Stopp**, **zur Station** oder **Raum-Warteschlange abbrechen** beenden die Warteschlange.
- Wird der Roboter in der App gestoppt, endet die Warteschlange ebenfalls.

### Per Aktion (Skripte und Automationen)

```yaml
action: ecovacs_omni.clean_rooms
target:
  entity_id: vacuum.robbi
data:
  rooms: ["Küche", "Flur", "Wohnzimmer"]   # Reihenfolge = Liste
  sequential: true          # optional, sonst gilt der Schalter
  work_mode: vacuum_and_mop # optional: vacuum | mop | vacuum_and_mop | mop_after_vacuum
  fan_speed: max            # optional: quiet | normal | max | max_plus
  water_amount: 30          # optional, T50: 0–50
  cleanings: 1              # optional, Durchgänge
```

Weitere Aktionen:

| Aktion | Zweck |
|---|---|
| `ecovacs_omni.clean_selected_rooms` | Reinigt die per Schalter ausgewählten Räume (gleiche optionale Felder) |
| `ecovacs_omni.select_rooms` | Setzt die Raumauswahl samt Reihenfolge, z. B. `rooms: ["Bad", "Flur"]` |
| `ecovacs_omni.cancel_room_queue` | Bricht die Warteschlange ab; der aktuelle Raum wird noch fertig |
| `ecovacs_omni.station_action` | `action: empty_dustbin \| wash_mop \| dry_mop \| clean_base` |
| `ecovacs_omni.get_rooms` | Liefert alle Räume mit IDs (in *Entwicklerwerkzeuge → Aktionen* ausprobieren) |

Statt Namen gehen auch Raum-IDs (`rooms: [3, 1]`). Groß- und Kleinschreibung spielt keine Rolle.

### Sprachsteuerung und HA-Bereiche

Der Roboter unterstützt auch die eingebaute Aktion `vacuum.clean_area` von Home Assistant:

1. In den Einstellungen der Staubsauger-Entität die Ecovacs-Räume den HA-Bereichen zuordnen.
2. Danach funktionieren z. B. Assist-Befehle wie „Reinige die Küche“.

## Hinweise und Einschränkungen

- **Raumbefehl:** Neuere Modelle wie der T50 MAX PRO OMNI lehnen den Raumbefehl `spotArea` ab,
  den `deebot-client` und die offizielle Integration verwenden (Fehler 20011 „unknown type“).
  Diese Integration sendet deshalb wie die Ecovacs-App `freeClean` im Format `1,<Raum>;1,<Raum>`.
  Die Durchgänge stellt sie vorher über `setCleanCount` ein. Kennt ein Roboter `freeClean` nicht,
  wechselt sie automatisch auf `spotArea` und merkt sich das. Welche Variante aktiv ist, steht in
  den Diagnosedaten unter `clean_type`.

- **Nacheinander-Modus:** Nach jedem Raum meldet der Roboter „fertig“ und will zur Station zurück.
  Die Integration schickt sofort den nächsten Raum. Je nach Firmware fährt der T50 vorher kurz zur
  Station, z. B. um die Mopps zu waschen. Das kostet etwas Zeit, sichert aber die Reihenfolge.
- **Station-Buttons:** `deebot-client` listet für den T50 offiziell nur „Staub absaugen“. Mopps
  waschen, Mopps trocknen und Station reinigen werden trotzdem angeboten, weil die Station sie
  beherrscht. Das Attribut `listed_by_library` zeigt, welche davon ungetestet sind. Bitte melden,
  ob sie bei dir funktionieren.
- **Cloud:** Ohne Internet oder bei einer Störung der Ecovacs-Server ist der Roboter nicht steuerbar.

## Fehlersuche

Ausführliches Logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.ecovacs_omni: debug
    deebot_client: debug
```

Unter *Gerät → Diagnosedaten herunterladen* bekommst du eine Datei ohne Zugangsdaten. Sie enthält
Gerätemodell, Fähigkeiten und Räume und hilft bei Fehlerberichten.

## Entwicklung

```bash
uv venv -p 3.14 && uv pip install -r requirements_test.txt
.venv/bin/python -m pytest
uvx ruff check . && uvx ruff format --check .
```

Basiert auf [`deebot-client`](https://github.com/DeebotUniverse/client.py) und orientiert sich an
der offiziellen Ecovacs-Integration von Home Assistant (Apache-2.0).
