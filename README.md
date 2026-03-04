# DahuaVTO – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Vollständige Home Assistant Integration für **GOLIATH / Dahua VTO** Türsprechanlagen (auch OEM-Varianten).
Full Home Assistant integration for **GOLIATH / Dahua VTO** door stations (including OEM variants).

---

## Unterstützte Geräte / Supported Devices

| Hersteller / Manufacturer | Modell / Model | Getestet / Tested |
|---|---|---|
| GOLIATH | AV-VTA05-22AV2 (Hybrid Modular) | ✅ |
| Dahua | VTO2202F, VTO2111D, VTO2000A | ✅ (reported) |
| Alle Dahua VTO | mit CGI-API und HTTP-Digest-Auth | ✅ |

---

## Features

### Entitäten / Entities

| Plattform | Entität (DE) | Entity (EN) | Bedingung |
|---|---|---|---|
| `event` | Klingel / Klingel Button N | Doorbell / Doorbell button N | Immer |
| `event` | Fingerabdruck | Fingerprint | Hat Fingerprint-Modul |
| `event` | Kartenzugang | Card access | Hat RFID-Leser |
| `binary_sensor` | Klingel | Doorbell | Immer (5 s Auto-Reset) |
| `binary_sensor` | Türkontakt | Door contact | Immer |
| `binary_sensor` | Anruf unbeantwortet | Missed call | Immer |
| `button` | Tür öffnen | Open door | Immer |
| `sensor` | Letzter Zugang | Last access | Immer |
| `camera` | Türkamera | Door camera | Immer (RTSP) |
| `image` | Türkamera Snapshot | Door camera snapshot | Auto-Update bei Klingeln |

### Services

| Service | Beschreibung |
|---|---|
| `dahua_vto.learn_card` | Kartenlernmodus starten (nächste Karte wird registriert) |
| `dahua_vto.stop_learn_card` | Kartenlernmodus abbrechen |
| `dahua_vto.add_card` | Karte direkt per Nummer registrieren |
| `dahua_vto.delete_card` | Karte löschen |
| `dahua_vto.enroll_fingerprint` | Fingerabdruck-Enrollment starten |
| `dahua_vto.cancel_enrollment` | Laufendes Enrollment abbrechen |
| `dahua_vto.list_users` | Alle Benutzer vom Gerät laden (feuert `dahua_vto_users_listed`) |

### HA-Bus-Events (für Automationen)

| Event | Wann |
|---|---|
| `dahua_vto_card_learned` | Karte erkannt im Lernmodus |
| `dahua_vto_fingerprint_enrolled` | Fingerabdruck-Enrollment erfolgreich |
| `dahua_vto_fingerprint_failed` | Enrollment fehlgeschlagen / Timeout |
| `dahua_vto_doorbell_snapshot` | Snapshot nach Klingeln fertig |
| `dahua_vto_users_listed` | Benutzerliste abgerufen |

---

## Installation

### Option A: HACS (empfohlen / recommended)

1. HACS → **Integrationen** → **⋮** → **Custom repositories**
2. URL: `https://github.com/itsh-neumeier/dahua_vto` | Kategorie: **Integration**
3. Integration installieren → HA neu starten
4. **Einstellungen → Geräte & Dienste → + Integration hinzufügen → DahuaVTO**

### Option B: Manuell

1. Ordner `custom_components/dahua_vto/` in dein HA-Konfigurationsverzeichnis kopieren
2. HA neu starten
3. **Einstellungen → Geräte & Dienste → + Integration hinzufügen → DahuaVTO**

---

## Konfiguration / Configuration

Der Config Flow erkennt alle Module automatisch:

1. **Schritt 1:** IP-Adresse, Port (Standard: 80), Benutzername, Passwort eingeben
2. **Schritt 2:** Erkannte Module prüfen und ggf. korrigieren
   - Anzahl Klingel-Buttons
   - Fingerabdruck-Modul vorhanden
   - RFID-/Karten-Leser vorhanden

---

## Automations-Beispiele / Automation Examples

### Benachrichtigung bei Klingeln mit Snapshot

```yaml
automation:
  - alias: "Klingel – Push mit Foto"
    trigger:
      - platform: event
        event_type: dahua_vto_doorbell_snapshot
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "Jemand klingelt!"
          message: "Klingel wurde gedrückt"
          data:
            image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
```

### Tür öffnen wenn bekannter Fingerabdruck

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

### Neue Karte per Service registrieren

```yaml
service: dahua_vto.add_card
data:
  card_no: "E30EC100"
  user_id: "1015"
  user_name: "Max Mustermann"
```

---

## Technische Details / Technical Details

### Protokoll / Protocol

- **Authentifizierung:** HTTP Digest Authentication (RFC 2617)
- **Event-Stream:** `GET /cgi-bin/eventManager.cgi?action=attach&codes=[...]`
  Multipart/mixed persistente Verbindung, Auto-Reconnect mit exponentialem Backoff
- **Benutzerverwaltung:** Dahua RPC2 API (`/RPC2_Login` → `/RPC2`)
- **Türöffnen:** `GET /cgi-bin/accessControl.cgi?action=openDoor`
- **Kamera:** RTSP `rtsp://user:pass@ip:554/cam/realmonitor?channel=1&subtype=0`

### Sicherheitshinweise / Security Notes

> ⚠️ **HTTP only** – Das Gerät unterstützt kein HTTPS. Die Kommunikation läuft unverschlüsselt im lokalen Netzwerk. HTTP Digest Auth schützt das Passwort vor direktem Mitlesen, Event-Daten sind jedoch im Klartext.
>
> 🔒 **Empfehlung:** Gerät in einem separaten IoT-VLAN betreiben und den Zugriff auf Home Assistant beschränken.

- Credentials werden in HA's verschlüsseltem Config-Entry-Storage gespeichert
- RTSP-URL enthält Credentials – HA-Zugriff absichern (Authentifizierung, VPN)
- Services zur Zutrittskontrolle (Karte, Fingerabdruck) nur vertrauenswürdigen HA-Benutzern erlauben

---

## Bekannte Besonderheiten / Known Quirks

| Firmware | Klingel-Event | Hinweis |
|---|---|---|
| GOLIATH Hybrid | `CallNoAnswered;action=Start` | Standard-Klingelevents nicht vorhanden |
| Standard Dahua VTO | `BackKeyLight;action=Start` | Üblich |
| Manche VTO | `RingBell;action=Pulse` | Alternativ |

- **Multi-Line JSON:** Eventdaten werden vom Gerät über mehrere Zeilen verteilt gesendet – die Integration puffert sie korrekt.
- **Button-Indizierung:** Manche Firmware nutzt 0-basiert, andere 1-basiert – beide werden erkannt.

---

## Versionshistorie / Changelog

| Version | Änderungen |
|---|---|
| 1.9.0 | Zweisprachigkeit (DE/EN), HACS-Dokumentation, Security-Verbesserungen (URL-Sanitisierung), NumberSelector BOX statt Slider |
| 1.8.0 | Multi-line JSON Stream-Parser, DoorStatus Pulse-Handling |
| 1.7.0 | CallNoAnswered als Klingel-Event, VideoTalk in EVENT_CODES, Tippfehler-Fix |
| 1.6.0 | UserID→UserName Mapping (user_map), Sensor zeigt Benutzernamen |
| 1.0.0 | Erstveröffentlichung |

---

## Lizenz / License

MIT License – Copyright (c) 2026 Timo Neumeier – siehe [LICENSE](LICENSE)
