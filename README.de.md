# DahuaVTO – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.13.0-brightgreen.svg)](https://github.com/itsh-neumeier/dahua_vto/releases)

> 🇬🇧 English documentation: [README.md](README.md)

Vollständige Home Assistant Integration für **GOLIATH / Dahua VTO** Türsprechanlagen (inkl. OEM-Varianten).

---

## Unterstützte Geräte

| Hersteller | Modell | Getestet |
|---|---|---|
| GOLIATH | AV-VTA05-22AV2 (Hybrid Modular) | ✅ |
| Dahua | VTO2202F, VTO2111D, VTO2000A | ✅ (gemeldet) |
| Alle Dahua VTO | mit CGI API und HTTP Digest Auth | ✅ |

---

## Funktionen

### Entitäten

| Plattform | Entität | Bedingung |
|---|---|---|
| `event` | Klingel / Klingel Button N | Immer |
| `event` | Fingerabdruck | Fingerabdruck-Modul vorhanden |
| `event` | Kartenzugang | RFID-Leser vorhanden |
| `binary_sensor` | Klingel | Immer (5 s Auto-Reset) |
| `binary_sensor` | Türkontakt | Immer |
| `binary_sensor` | Anruf unbeantwortet | Immer |
| `binary_sensor` | Im Gespräch | Immer |
| `button` | Tür öffnen | Immer |
| `lock` | Türschloss | Immer (Entsperren = Relais-Impuls, 5 s Auto-Reset) |
| `sensor` | Letzter Zugang | Immer |
| `camera` | Türkamera | Immer (RTSP) |
| `image` | Türkamera Snapshot | Auto-Update bei Klingeln |

### Dienste

| Dienst | Beschreibung |
|---|---|
| `dahua_vto.learn_card` | Karten-Anlernmodus starten (nächste Karte wird registriert) |
| `dahua_vto.stop_learn_card` | Karten-Anlernmodus abbrechen |
| `dahua_vto.add_card` | Karte direkt per Nummer registrieren |
| `dahua_vto.delete_card` | Karte löschen |
| `dahua_vto.enroll_fingerprint` | Fingerabdruck-Einlernvorgang starten |
| `dahua_vto.cancel_enrollment` | Laufenden Einlernvorgang abbrechen |
| `dahua_vto.list_users` | Alle Benutzer vom Gerät laden (löst `dahua_vto_users_listed` aus) |
| `dahua_vto.call_room` | Video-Anruf zur VTH-Innenstation starten |
| `dahua_vto.stop_call` | Laufenden Anruf beenden |
| `dahua_vto.trigger_alarm` | Alarmausgang ein-/ausschalten (mit optionaler Auto-Stop-Dauer) |
| `dahua_vto.get_logs` | Ereignisprotokoll abrufen (löst `dahua_vto_logs_fetched` aus) |

> **Tipp:** Bei `learn_card` und `enroll_fingerprint` genügt die Angabe von `user_name`. Die Integration ermittelt automatisch die passende UserID oder generiert eine neue.

### HA Bus-Events (für Automationen)

| Event | Wann |
|---|---|
| `dahua_vto_card_learned` | Karte im Anlernmodus erkannt |
| `dahua_vto_fingerprint_enrolled` | Fingerabdruck erfolgreich eingelernt |
| `dahua_vto_fingerprint_failed` | Einlernvorgang fehlgeschlagen / Timeout |
| `dahua_vto_doorbell_snapshot` | Snapshot bereit nach Klingeln |
| `dahua_vto_users_listed` | Benutzerliste abgerufen |
| `dahua_vto_logs_fetched` | Protokolleinträge via `get_logs`-Dienst abgerufen |

---

## Installation

### Option A: HACS (empfohlen)

1. HACS → **Integrationen** → **⋮** → **Benutzerdefinierte Repositories**
2. URL: `https://github.com/itsh-neumeier/dahua_vto` | Kategorie: **Integration**
3. Integration installieren → HA neu starten
4. **Einstellungen → Geräte & Dienste → + Integration hinzufügen → DahuaVTO**

### Option B: Manuell

1. Ordner `custom_components/dahua_vto/` in das HA-Konfigurationsverzeichnis kopieren
2. HA neu starten
3. **Einstellungen → Geräte & Dienste → + Integration hinzufügen → DahuaVTO**

---

## Konfiguration

Der Konfigurationsassistent erkennt alle Module automatisch:

1. **Schritt 1:** IP-Adresse, Port (Standard: 80), Benutzername, Passwort eingeben
2. **Schritt 2:** Erkannte Module prüfen und ggf. korrigieren
   - Anzahl der Klingel-Buttons
   - Fingerabdruck-Modul vorhanden
   - RFID-/Karten-Leser vorhanden

---

## Automations-Beispiele

> **So verwenden:** Jeden Block direkt in den HA Automation-YAML-Editor einfügen:
> **Einstellungen → Automationen → Neue Automation → ⋮ → In YAML bearbeiten**
> `av_vta05_22av2` durch den tatsächlichen Gerätenamen ersetzen, `notify.mobile_app_mein_handy` durch den eigenen Dienst.

---

### 🔔 Klingel

#### Push-Benachrichtigung mit Foto

```yaml
alias: "Klingel – Push-Benachrichtigung mit Foto"
description: "Push-Benachrichtigung mit Snapshot bei Klingeln"
mode: single
triggers:
  - trigger: event
    event_type: dahua_vto_doorbell_snapshot
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "Jemand an der Tür!"
      message: "Die Klingel wurde gedrückt"
      data:
        image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
```

#### Innenlichter bei Klingeln kurz blinken lassen

```yaml
alias: "Klingel – Lichter blinken"
description: "Wohnzimmerlicht kurz blinken wenn geklingelt wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions: []
actions:
  - action: light.turn_on
    target:
      entity_id: light.wohnzimmer
    data:
      flash: short
```

#### Sprachansage bei Klingeln (TTS)

```yaml
alias: "Klingel – Sprachansage"
description: "Sprachansage über Smart Speaker wenn geklingelt wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions: []
actions:
  - action: tts.speak
    target:
      entity_id: media_player.echo_dot
    data:
      message: "Es hat jemand an der Haustür geklingelt!"
```

#### Außenbeleuchtung nachts einschalten

```yaml
alias: "Klingel – Außenlicht nachts"
description: "Außenlicht 5 Minuten einschalten wenn nachts geklingelt wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions:
  - condition: sun
    after: sunset
    before: sunrise
actions:
  - action: light.turn_on
    target:
      entity_id: light.hauseingang
  - delay: "00:05:00"
  - action: light.turn_off
    target:
      entity_id: light.hauseingang
```

#### 30-Sekunden-Kameraaufnahme bei Klingeln starten

```yaml
alias: "Klingel – Kameraaufnahme 30 s"
description: "30-Sekunden-Videoaufnahme starten wenn geklingelt wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions: []
actions:
  - action: camera.record
    target:
      entity_id: camera.av_vta05_22av2_tuerkamera
    data:
      filename: "/config/www/aufnahmen/klingel_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4"
      duration: 30
```

#### Innenstation automatisch anrufen (Klingel-Durchschaltung)

```yaml
alias: "Klingel – Innenstation automatisch anrufen"
description: "Bei Klingeln automatisch die VTH-Innenstation anrufen"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions: []
actions:
  - action: dahua_vto.call_room
    data:
      room_no: "8020"
```

---

### 🔑 Zugangskontrolle

#### Tür öffnen bei bekanntem Fingerabdruck (gewährt)

```yaml
alias: "Fingerabdruck – Tür öffnen"
description: "Tür automatisch öffnen wenn Fingerabdruck akzeptiert wird"
mode: single
triggers:
  - trigger: state
    entity_id: event.av_vta05_22av2_fingerabdruck
    attribute: event_type
    to: "granted"
conditions: []
actions:
  - action: button.press
    target:
      entity_id: button.av_vta05_22av2_tur_offnen
```

#### Benachrichtigung bei Zugang mit Benutzername

```yaml
alias: "Zugang – Benachrichtigung mit Name"
description: "Benachrichtigung wenn jemand die Tür öffnet, mit Benutzername"
mode: single
triggers:
  - trigger: state
    entity_id: sensor.av_vta05_22av2_letzter_zugang
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "Tür geöffnet"
      message: >
        {{ states('sensor.av_vta05_22av2_letzter_zugang') }}
        via {{ state_attr('sensor.av_vta05_22av2_letzter_zugang', 'method') }}
        um {{ now().strftime('%H:%M') }} Uhr.
```

#### Alarm bei abgewiesenem Zugang

```yaml
alias: "Zugang – Abgewiesen Alarm"
description: "Alarm wenn unbekannte Karte oder Fingerabdruck abgewiesen wird"
mode: single
triggers:
  - trigger: state
    entity_id: event.av_vta05_22av2_kartenzugang
    attribute: event_type
    to: "denied"
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "⚠️ Zugang verweigert"
      message: "Eine unbekannte Karte oder ein unbekannter Fingerabdruck wurde abgewiesen."
```

#### Tür für bestimmte Person automatisch öffnen

```yaml
alias: "Zugang – Tür automatisch für Timo öffnen"
description: "Türschloss automatisch entsperren wenn Timo zugreift"
mode: single
triggers:
  - trigger: state
    entity_id: sensor.av_vta05_22av2_letzter_zugang
conditions:
  - condition: template
    value_template: >
      {{ state_attr('sensor.av_vta05_22av2_letzter_zugang', 'user_name') == 'TimoNeumeier' }}
actions:
  - action: lock.unlock
    target:
      entity_id: lock.av_vta05_22av2_tuerschloss
```

#### Karten-Anlernmodus starten (Dienstaufruf)

> Dies ist ein **Dienstaufruf**, keine Automation. Ausführen über **Entwicklerwerkzeuge → Dienste** oder aus einer anderen Automation heraus.

```yaml
action: dahua_vto.learn_card
data:
  user_name: "Max Mustermann"
  timeout: 30
```

---

### 📞 Anrufe

#### Benachrichtigung bei unbeantworteten Anrufen

```yaml
alias: "Anruf – Benachrichtigung verpasster Anruf"
description: "Benachrichtigung wenn jemand geklingelt hat und nicht aufgemacht wurde"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_anruf_unbeantwortet
    to: "on"
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "📞 Verpasster Anruf an der Tür"
      message: "Jemand hat geklingelt und niemand hat aufgemacht."
```

#### TV-Lautstärke während des Gesprächs reduzieren

```yaml
alias: "Gespräch – TV leiser schalten"
description: "TV-Lautstärke reduzieren wenn ein Türanruf aktiv ist"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_im_gesprach
    to: "on"
conditions: []
actions:
  - action: media_player.volume_set
    target:
      entity_id: media_player.wohnzimmer_tv
    data:
      volume_level: 0.1
```

#### TV-Lautstärke nach Gespräch wiederherstellen

```yaml
alias: "Gespräch – TV-Lautstärke wiederherstellen"
description: "TV-Lautstärke wiederherstellen wenn Türanruf beendet wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_im_gesprach
    to: "off"
conditions: []
actions:
  - action: media_player.volume_set
    target:
      entity_id: media_player.wohnzimmer_tv
    data:
      volume_level: 0.5
```

#### Anruf automatisch nach 60 Sekunden beenden

```yaml
alias: "Anruf – Automatisch auflegen nach 60 s"
description: "Anruf automatisch beenden wenn er länger als 60 Sekunden aktiv ist"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_im_gesprach
    to: "on"
    for: "00:01:00"
conditions: []
actions:
  - action: dahua_vto.stop_call
```

#### Innenstation anrufen wenn Hilfsschalter gedrückt wird

```yaml
alias: "Ankündigung – Paketlieferung"
description: "Innenstation anrufen wenn Ankündigungs-Schalter gedrückt wird"
mode: single
triggers:
  - trigger: state
    entity_id: input_button.ankuendigung_paket
conditions: []
actions:
  - action: dahua_vto.call_room
    data:
      room_no: "8020"
```

---

### 🚨 Alarm

#### Alarm bei nächtlichem Klingeln auslösen

```yaml
alias: "Sicherheit – Alarm bei nächtlichem Klingeln"
description: "Alarmausgang schalten wenn zwischen 23:00 und 06:00 Uhr geklingelt wird"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_klingel
    to: "on"
conditions:
  - condition: time
    after: "23:00:00"
    before: "06:00:00"
actions:
  - action: dahua_vto.trigger_alarm
    data:
      channel: 1
      active: true
      duration: 30
  - action: notify.mobile_app_mein_handy
    data:
      title: "🚨 Nacht-Alarm!"
      message: "Jemand hat nachts geklingelt – Alarm ausgelöst."
```

#### Alarm stoppen wenn Hilfsschalter gedrückt wird

```yaml
alias: "Sicherheit – Türalarm stoppen"
description: "Alarmausgang abschalten wenn Stopp-Schalter gedrückt wird"
mode: single
triggers:
  - trigger: state
    entity_id: input_button.alarm_stoppen
conditions: []
actions:
  - action: dahua_vto.trigger_alarm
    data:
      channel: 1
      active: false
```

---

### 🚪 Türkontakt

#### Benachrichtigung wenn Tür länger als 5 Minuten offen

```yaml
alias: "Tür – Alarm wenn länger als 5 Minuten offen"
description: "Benachrichtigung wenn Türkontakt länger als 5 Minuten aktiv ist"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_tuerkontakt
    to: "on"
    for: "00:05:00"
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "🚪 Tür noch offen"
      message: "Die Haustür ist seit mehr als 5 Minuten geöffnet!"
```

#### Snapshot wenn Türrelais schaltet

```yaml
alias: "Tür – Snapshot bei Öffnung"
description: "Kamera-Snapshot speichern wenn das Türrelais auslöst"
mode: single
triggers:
  - trigger: state
    entity_id: binary_sensor.av_vta05_22av2_tuerkontakt
    to: "on"
conditions: []
actions:
  - action: camera.snapshot
    target:
      entity_id: camera.av_vta05_22av2_tuerkamera
    data:
      filename: "/config/www/snapshots/tuer_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg"
```

---

### 🛠️ Verwaltung

#### Benachrichtigung wenn Karte angelernt wurde oder Timeout

```yaml
alias: "Verwaltung – Karte angelernt"
description: "Benachrichtigung wenn Karten-Anlernvorgang erfolgreich war oder Timeout"
mode: single
triggers:
  - trigger: event
    event_type: dahua_vto_card_learned
conditions: []
actions:
  - choose:
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.status == 'learned' }}"
        sequence:
          - action: notify.mobile_app_mein_handy
            data:
              title: "✅ Karte registriert"
              message: >
                Karte {{ trigger.event.data.card_no }}
                wurde Benutzer {{ trigger.event.data.user_id }} zugewiesen.
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.status == 'timeout' }}"
        sequence:
          - action: notify.mobile_app_mein_handy
            data:
              title: "⏱️ Karten-Lernmodus Timeout"
              message: "Innerhalb der Zeit wurde keine Karte vorgehalten."
```

#### Benachrichtigung wenn Fingerabdruck erfolgreich eingelernt

```yaml
alias: "Verwaltung – Fingerabdruck eingelernt"
description: "Benachrichtigung wenn Fingerabdruck-Einlernen erfolgreich war"
mode: single
triggers:
  - trigger: event
    event_type: dahua_vto_fingerprint_enrolled
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "✅ Fingerabdruck eingelernt"
      message: >
        Finger {{ trigger.event.data.finger_index }} für
        {{ trigger.event.data.user_name }} erfolgreich gespeichert.
```

#### Benachrichtigung wenn Fingerabdruck-Einlernen fehlschlägt

```yaml
alias: "Verwaltung – Fingerabdruck-Einlernen fehlgeschlagen"
description: "Benachrichtigung wenn Fingerabdruck-Einlernen fehlschlägt oder Timeout"
mode: single
triggers:
  - trigger: event
    event_type: dahua_vto_fingerprint_failed
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "❌ Fingerabdruck-Einlernen fehlgeschlagen"
      message: >
        Einlernen für Benutzer {{ trigger.event.data.user_id }}
        fehlgeschlagen: {{ trigger.event.data.reason }}.
```

#### Benutzerliste beim HA-Start laden

```yaml
alias: "Verwaltung – Benutzer beim Start laden"
description: "Benutzerliste vom Gerät laden wenn HA startet"
mode: single
triggers:
  - trigger: homeassistant
    event: start
conditions: []
actions:
  - delay: "00:00:30"
  - action: dahua_vto.list_users
```

---

### 📋 Ereignisprotokoll

#### Zugriffslog stündlich abrufen

```yaml
alias: "Protokoll – Stündlich abrufen"
description: "In-Memory Ereignisprotokoll jede Stunde abrufen"
mode: single
triggers:
  - trigger: time_pattern
    hours: "/1"
conditions: []
actions:
  - action: dahua_vto.get_logs
    data:
      count: 50
      source: memory
```

#### Persistente Benachrichtigung mit letztem Log-Eintrag

```yaml
alias: "Protokoll – Letzten Eintrag anzeigen"
description: "Persistente Benachrichtigung nach Log-Abruf mit letztem Eintrag"
mode: single
triggers:
  - trigger: event
    event_type: dahua_vto_logs_fetched
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.count > 0 }}"
actions:
  - action: notify.persistent_notification
    data:
      title: "DahuaVTO – Zugriffsprotokoll ({{ trigger.event.data.count }} Einträge)"
      message: >
        {% set last = trigger.event.data.records | last %}
        Letzter Eintrag: {{ last.timestamp }}
        – {{ last.code }}/{{ last.action }}
        {% if last.user_name %} ({{ last.user_name }}){% endif %}
```

---

## Technische Details

### Protokoll

- **Authentifizierung:** HTTP Digest Authentication (RFC 2617)
- **Event-Stream:** `GET /cgi-bin/eventManager.cgi?action=attach&codes=[...]`
  Persistente Multipart/Mixed-Verbindung mit automatischem Exponential-Backoff-Reconnect
- **Benutzerverwaltung:** Dahua RPC2 API (`/RPC2_Login` → `/RPC2`)
- **Türöffnen:** `GET /cgi-bin/accessControl.cgi?action=openDoor`
- **Kamera:** RTSP `rtsp://user:pass@ip:554/cam/realmonitor?channel=1&subtype=0`

### Sicherheitshinweise

> ⚠️ **Nur HTTP** – Das Gerät unterstützt kein HTTPS. Die Kommunikation läuft unverschlüsselt im lokalen Netzwerk. HTTP Digest Auth schützt das Passwort vor direktem Abfangen, aber Event-Daten werden im Klartext übertragen.
>
> 🔒 **Empfehlung:** Gerät in einem separaten IoT-VLAN betreiben und den Zugriff auf Home Assistant beschränken.

- Zugangsdaten werden im verschlüsselten HA Config-Entry-Speicher abgelegt
- Die RTSP-URL enthält Zugangsdaten – HA-Instanz absichern (Authentifizierung, VPN)
- Zugriffskontrolldienste (Karte, Fingerabdruck) nur vertrauenswürdigen HA-Benutzern zugänglich machen

---

## Bekannte Besonderheiten

| Firmware | Klingel-Event | Hinweis |
|---|---|---|
| GOLIATH Hybrid | `CallNoAnswered;action=Start` | Standardmäßige Klingel-Events nicht vorhanden |
| Standard Dahua VTO | `BackKeyLight;action=Start` | Üblich |
| Manche VTO | `RingBell;action=Pulse` | Alternative |

- **Mehrzeiliges JSON:** Das Gerät sendet Event-Daten über mehrere Zeilen verteilt – die Integration puffert diese korrekt.
- **Button-Nummerierung:** Manche Firmwares verwenden 0-basierte, andere 1-basierte Indizes – beide werden erkannt.
- **Türkontakt:** GOLIATH sendet `DoorStatus;action=Pulse;data={"Relay":true}` wenn das Relais schaltet. Der Sensor zeigt 8 Sekunden lang OFFEN, da kein physischer Kontaktsensor angeschlossen ist.
- **Im-Gespräch-Sensor:** Bei GOLIATH-Firmware erscheinen `VideoTalk`-Events möglicherweise nur, wenn ein SIP-Client tatsächlich verbunden ist. Der Sensor schaltet sofort bei `CallNoAnswered` auf AUS.

---

## Changelog

| Version | Änderungen |
|---|---|
| **1.13.0** | Geräte-Log-Abruf korrigiert: korrekter RecordFinder-API-Ablauf (`factory.create` → objektbasierte Aufrufe, Record-Typen `AccessControlCardRec` / `VideoTalkLog`). Neuer `log_type`-Parameter für `get_logs` (`access` / `call` / `all`). HAR-Analyse bestätigte 887 Anruf-Logs + 1000 Zugangs-Logs auf dem Gerät. |
| **1.12.0** | Türöffnungs-Logging: `lock.unlock` und `button.open_door` schreiben jetzt einen expliziten `DoorUnlock`-Eintrag (mit Quelle `lock`/`button`) ins In-Memory-Log – sichtbar in der `get_logs`-Ausgabe |
| **1.11.0** | Neue Lock-Entität (Türrelais als HA-Schloss, 5 s Auto-Reset), neue Dienste: `call_room`, `stop_call`, `trigger_alarm` (mit Auto-Stop-Dauer), `get_logs` (In-Memory + RPC2-Log, löst `dahua_vto_logs_fetched` aus) |
| **1.10.0** | Snapshot-Fix (CallNoAnswered in image.py), Türkontakt via Relay=true (8s Auto-Reset), neuer „Im Gespräch"-Binärsensor, automatische UserID-Auflösung per Name für Karten-/Fingerabdruck-Dienste |
| **1.9.0** | Zweisprachig DE/EN via HA-Übersetzungssystem, HACS-Dokumentation, Sicherheitsverbesserungen (URL-Sanitierung), NumberSelector BOX statt Schieberegler |
| **1.8.0** | Mehrzeiliger JSON-Stream-Parser, DoorStatus Pulse-Behandlung |
| **1.7.0** | CallNoAnswered als Klingel-Event, VideoTalk in EVENT_CODES, Tippfehler-Fix |
| **1.6.0** | UserID→UserName-Zuordnung (user_map), Sensor zeigt Benutzernamen |
| **1.5.0** | Erste öffentliche Version |

---

## Lizenz

MIT License – Copyright (c) 2026 Timo Neumeier – siehe [LICENSE](LICENSE)
