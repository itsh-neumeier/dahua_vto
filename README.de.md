# DahuaVTO – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.11.0-brightgreen.svg)](https://github.com/itsh-neumeier/dahua_vto/releases)

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

> **Hinweis:** Entitäts-IDs hängen vom Gerätemodell ab. `av_vta05_22av2` durch den tatsächlichen Gerätenamen in HA ersetzen. `notify.mobile_app_mein_handy` durch den eigenen Benachrichtigungsdienst ersetzen.

---

### 🔔 Klingel

#### Push-Benachrichtigung mit Foto

```yaml
automation:
  - alias: "Klingel – Push-Benachrichtigung mit Foto"
    trigger:
      - platform: event
        event_type: dahua_vto_doorbell_snapshot
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "Jemand an der Tür!"
          message: "Die Klingel wurde gedrückt"
          data:
            image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
```

#### Innenlichter bei Klingeln kurz blinken lassen

```yaml
automation:
  - alias: "Klingel – Lichter blinken"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.wohnzimmer
        data:
          flash: short
```

#### Sprachansage bei Klingeln (TTS)

```yaml
automation:
  - alias: "Klingel – Sprachansage"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    action:
      - service: tts.speak
        target:
          entity_id: media_player.echo_dot
        data:
          message: "Es hat jemand an der Haustür geklingelt!"
```

#### Außenbeleuchtung nachts einschalten

```yaml
automation:
  - alias: "Klingel – Außenlicht nachts"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    condition:
      - condition: sun
        after: sunset
        before: sunrise
    action:
      - service: light.turn_on
        target:
          entity_id: light.hauseingang
      - delay: "00:05:00"
      - service: light.turn_off
        target:
          entity_id: light.hauseingang
```

#### 30-Sekunden-Kameraaufnahme bei Klingeln starten

```yaml
automation:
  - alias: "Klingel – Kameraaufnahme 30 s"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    action:
      - service: camera.record
        target:
          entity_id: camera.av_vta05_22av2_tuerkamera
        data:
          filename: "/config/www/aufnahmen/klingel_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4"
          duration: 30
```

#### Innenstation automatisch anrufen (Klingel-Durchschaltung)

```yaml
automation:
  - alias: "Klingel – Innenstation automatisch anrufen"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    action:
      - service: dahua_vto.call_room
        data:
          room_no: "8020"
```

---

### 🔑 Zugangskontrolle

#### Tür öffnen bei bekanntem Fingerabdruck (gewährt)

```yaml
automation:
  - alias: "Fingerabdruck – Tür öffnen"
    trigger:
      - platform: state
        entity_id: event.av_vta05_22av2_fingerabdruck
        attribute: event_type
        to: "granted"
    action:
      - service: button.press
        target:
          entity_id: button.av_vta05_22av2_tur_offnen
```

#### Benachrichtigung bei Zugang mit Benutzername

```yaml
automation:
  - alias: "Zugang – Benachrichtigung mit Name"
    trigger:
      - platform: state
        entity_id: sensor.av_vta05_22av2_letzter_zugang
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "Tür geöffnet"
          message: >
            {{ states('sensor.av_vta05_22av2_letzter_zugang') }}
            via {{ state_attr('sensor.av_vta05_22av2_letzter_zugang', 'method') }}
            um {{ now().strftime('%H:%M') }} Uhr.
```

#### Alarm bei abgewiesenem Zugang

```yaml
automation:
  - alias: "Zugang – Abgewiesen Alarm"
    trigger:
      - platform: state
        entity_id: event.av_vta05_22av2_kartenzugang
        attribute: event_type
        to: "denied"
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "⚠️ Zugang verweigert"
          message: "Eine unbekannte Karte oder ein unbekannter Fingerabdruck wurde abgewiesen."
```

#### Tür für bestimmte Person automatisch öffnen

```yaml
automation:
  - alias: "Zugang – Tür automatisch für Timo öffnen"
    trigger:
      - platform: state
        entity_id: sensor.av_vta05_22av2_letzter_zugang
    condition:
      - condition: template
        value_template: >
          {{ state_attr('sensor.av_vta05_22av2_letzter_zugang', 'user_name') == 'TimoNeumeier' }}
    action:
      - service: lock.unlock
        target:
          entity_id: lock.av_vta05_22av2_tuerschloss
```

#### Neue Karte nur per Name anlegen

```yaml
# Als Dienstaufruf – keine entity_id nötig
service: dahua_vto.learn_card
data:
  user_name: "Max Mustermann"
  timeout: 30
# Die Integration generiert automatisch eine UserID, falls "Max Mustermann" noch nicht vorhanden ist.
```

---

### 📞 Anrufe

#### Benachrichtigung bei unbeantworteten Anrufen

```yaml
automation:
  - alias: "Anruf – Benachrichtigung verpasster Anruf"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_anruf_unbeantwortet
        to: "on"
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "📞 Verpasster Anruf an der Tür"
          message: "Jemand hat geklingelt und niemand hat aufgemacht."
```

#### TV-Lautstärke während des Gesprächs reduzieren

```yaml
automation:
  - alias: "Gespräch – TV leiser schalten"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_im_gesprach
        to: "on"
    action:
      - service: media_player.volume_set
        target:
          entity_id: media_player.wohnzimmer_tv
        data:
          volume_level: 0.1

  - alias: "Gespräch – TV-Lautstärke wiederherstellen"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_im_gesprach
        to: "off"
    action:
      - service: media_player.volume_set
        target:
          entity_id: media_player.wohnzimmer_tv
        data:
          volume_level: 0.5
```

#### Anruf automatisch nach 60 Sekunden beenden

```yaml
automation:
  - alias: "Anruf – Automatisch auflegen nach 60 s"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_im_gesprach
        to: "on"
        for: "00:01:00"
    action:
      - service: dahua_vto.stop_call
```

#### Innenstation zur Ankündigung anrufen (z. B. Paketlieferung)

```yaml
script:
  paket_ansage:
    alias: "Ankündigung: Paket an der Tür"
    sequence:
      - service: dahua_vto.call_room
        data:
          room_no: "8020"
```

---

### 🚨 Alarm

#### Alarm bei nächtlichem Klingeln auslösen

```yaml
automation:
  - alias: "Sicherheit – Alarm bei nächtlichem Klingeln"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_klingel
        to: "on"
    condition:
      - condition: time
        after: "23:00:00"
        before: "06:00:00"
    action:
      - service: dahua_vto.trigger_alarm
        data:
          channel: 1
          active: true
          duration: 30        # Auto-Stop nach 30 Sekunden
      - service: notify.mobile_app_mein_handy
        data:
          title: "🚨 Nacht-Alarm!"
          message: "Jemand hat nachts geklingelt – Alarm ausgelöst."
```

#### Alarm manuell über Skript stoppen

```yaml
script:
  alarm_stoppen:
    alias: "Türalarm stoppen"
    sequence:
      - service: dahua_vto.trigger_alarm
        data:
          channel: 1
          active: false
```

---

### 🚪 Türkontakt

#### Benachrichtigung wenn Tür länger als 5 Minuten offen

```yaml
automation:
  - alias: "Tür – Alarm wenn länger als 5 Minuten offen"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_tuerkontakt
        to: "on"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "🚪 Tür noch offen"
          message: "Die Haustür ist seit mehr als 5 Minuten geöffnet!"
```

#### Snapshot wenn Türrelais schaltet

```yaml
automation:
  - alias: "Tür – Snapshot bei Öffnung"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_tuerkontakt
        to: "on"
    action:
      - service: camera.snapshot
        target:
          entity_id: camera.av_vta05_22av2_tuerkamera
        data:
          filename: "/config/www/snapshots/tuer_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg"
```

---

### 🛠️ Verwaltung

#### Benachrichtigung wenn Karte angelernt wurde

```yaml
automation:
  - alias: "Verwaltung – Karte angelernt"
    trigger:
      - platform: event
        event_type: dahua_vto_card_learned
    action:
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.status == 'learned' }}"
            sequence:
              - service: notify.mobile_app_mein_handy
                data:
                  title: "✅ Karte registriert"
                  message: >
                    Karte {{ trigger.event.data.card_no }}
                    wurde Benutzer {{ trigger.event.data.user_id }} zugewiesen.
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.status == 'timeout' }}"
            sequence:
              - service: notify.mobile_app_mein_handy
                data:
                  title: "⏱️ Karten-Lernmodus Timeout"
                  message: "Innerhalb der Zeit wurde keine Karte vorgehalten."
```

#### Benachrichtigung nach Fingerabdruck-Einlernvorgang

```yaml
automation:
  - alias: "Verwaltung – Fingerabdruck eingelernt"
    trigger:
      - platform: event
        event_type: dahua_vto_fingerprint_enrolled
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "✅ Fingerabdruck eingelernt"
          message: >
            Finger {{ trigger.event.data.finger_index }} für
            {{ trigger.event.data.user_name }} erfolgreich gespeichert.

  - alias: "Verwaltung – Fingerabdruck-Einlernen fehlgeschlagen"
    trigger:
      - platform: event
        event_type: dahua_vto_fingerprint_failed
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "❌ Fingerabdruck-Einlernen fehlgeschlagen"
          message: >
            Einlernen für Benutzer {{ trigger.event.data.user_id }}
            fehlgeschlagen: {{ trigger.event.data.reason }}.
```

#### Benutzerliste beim HA-Start laden

```yaml
automation:
  - alias: "Verwaltung – Benutzer beim Start laden"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - delay: "00:00:30"    # Warten bis Integration verbunden ist
      - service: dahua_vto.list_users
```

---

### 📋 Ereignisprotokoll

#### Zugriffslog stündlich abrufen

```yaml
automation:
  - alias: "Protokoll – Stündlich abrufen"
    trigger:
      - platform: time_pattern
        hours: "/1"
    action:
      - service: dahua_vto.get_logs
        data:
          count: 50
          source: memory    # immer verfügbar; "both" auch Gerät abfragen
```

#### Persistente Benachrichtigung nach Log-Abruf

```yaml
automation:
  - alias: "Protokoll – Letzten Eintrag anzeigen"
    trigger:
      - platform: event
        event_type: dahua_vto_logs_fetched
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.count > 0 }}"
    action:
      - service: notify.persistent_notification
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
