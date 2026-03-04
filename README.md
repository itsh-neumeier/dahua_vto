# DahuaVTO – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.10.0-brightgreen.svg)](https://github.com/itsh-neumeier/dahua_vto/releases)

> 🇩🇪 Deutsche Dokumentation: [README.de.md](README.de.md)

Full Home Assistant integration for **GOLIATH / Dahua VTO** door stations (including OEM variants).

---

## Supported Devices

| Manufacturer | Model | Tested |
|---|---|---|
| GOLIATH | AV-VTA05-22AV2 (Hybrid Modular) | ✅ |
| Dahua | VTO2202F, VTO2111D, VTO2000A | ✅ (reported) |
| All Dahua VTO | with CGI API and HTTP Digest Auth | ✅ |

---

## Features

### Entities

| Platform | Entity | Condition |
|---|---|---|
| `event` | Doorbell / Doorbell button N | Always |
| `event` | Fingerprint | Has fingerprint module |
| `event` | Card access | Has RFID reader |
| `binary_sensor` | Doorbell | Always (5 s auto-reset) |
| `binary_sensor` | Door contact | Always |
| `binary_sensor` | Missed call | Always |
| `binary_sensor` | Call active | Always |
| `button` | Open door | Always |
| `sensor` | Last access | Always |
| `camera` | Door camera | Always (RTSP) |
| `image` | Door camera snapshot | Auto-update on ring |

### Services

| Service | Description |
|---|---|
| `dahua_vto.learn_card` | Start card learning mode (next card scan is registered) |
| `dahua_vto.stop_learn_card` | Cancel card learning mode |
| `dahua_vto.add_card` | Register a card directly by number |
| `dahua_vto.delete_card` | Delete a card |
| `dahua_vto.enroll_fingerprint` | Start fingerprint enrollment |
| `dahua_vto.cancel_enrollment` | Cancel ongoing enrollment |
| `dahua_vto.list_users` | Load all users from device (fires `dahua_vto_users_listed`) |

> **Tip:** For `learn_card` and `enroll_fingerprint`, providing only `user_name` is sufficient. The integration automatically finds the matching UserID or generates a new one.

### HA Bus Events (for automations)

| Event | When |
|---|---|
| `dahua_vto_card_learned` | Card detected in learning mode |
| `dahua_vto_fingerprint_enrolled` | Fingerprint enrollment successful |
| `dahua_vto_fingerprint_failed` | Enrollment failed / timed out |
| `dahua_vto_doorbell_snapshot` | Snapshot ready after doorbell press |
| `dahua_vto_users_listed` | User list fetched |

---

## Installation

### Option A: HACS (recommended)

1. HACS → **Integrations** → **⋮** → **Custom repositories**
2. URL: `https://github.com/itsh-neumeier/dahua_vto` | Category: **Integration**
3. Install integration → Restart HA
4. **Settings → Devices & Services → + Add Integration → DahuaVTO**

### Option B: Manual

1. Copy folder `custom_components/dahua_vto/` into your HA config directory
2. Restart HA
3. **Settings → Devices & Services → + Add Integration → DahuaVTO**

---

## Configuration

The config flow detects all modules automatically:

1. **Step 1:** Enter IP address, port (default: 80), username, password
2. **Step 2:** Verify detected modules and correct if necessary
   - Number of doorbell buttons
   - Fingerprint module installed
   - RFID / card reader installed

---

## Automation Examples

### Doorbell notification with snapshot

```yaml
automation:
  - alias: "Doorbell – Push notification with photo"
    trigger:
      - platform: event
        event_type: dahua_vto_doorbell_snapshot
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "Someone at the door!"
          message: "Doorbell was pressed"
          data:
            image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
```

### Open door on known fingerprint

```yaml
automation:
  - alias: "Fingerprint – Open door"
    trigger:
      - platform: state
        entity_id: event.av_vta05_22av2_fingerprint
        attribute: event_type
        to: "granted"
    action:
      - service: button.press
        target:
          entity_id: button.av_vta05_22av2_open_door
```

### Register new card by name only

```yaml
service: dahua_vto.learn_card
data:
  user_name: "John Doe"
  timeout: 30
# The integration auto-generates a UserID if "John Doe" is not yet registered.
```

### Notify when call is active

```yaml
automation:
  - alias: "Doorbell call – notify if no answer after 15 s"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_call_active
        to: "off"
        for: "00:00:00"
    condition:
      - condition: state
        entity_id: binary_sensor.av_vta05_22av2_missed_call
        state: "on"
    action:
      - service: notify.mobile_app_my_phone
        data:
          message: "Missed call at the door!"
```

---

## Technical Details

### Protocol

- **Authentication:** HTTP Digest Authentication (RFC 2617)
- **Event stream:** `GET /cgi-bin/eventManager.cgi?action=attach&codes=[...]`
  Persistent multipart/mixed connection with automatic exponential-backoff reconnect
- **User management:** Dahua RPC2 API (`/RPC2_Login` → `/RPC2`)
- **Door unlock:** `GET /cgi-bin/accessControl.cgi?action=openDoor`
- **Camera:** RTSP `rtsp://user:pass@ip:554/cam/realmonitor?channel=1&subtype=0`

### Security Notes

> ⚠️ **HTTP only** – The device does not support HTTPS. Communication runs unencrypted on the local network. HTTP Digest Auth protects the password from direct interception, but event data is transmitted in plain text.
>
> 🔒 **Recommendation:** Operate the device in a separate IoT VLAN and restrict access to Home Assistant.

- Credentials are stored in HA's encrypted config entry storage
- RTSP URL contains credentials – secure your HA instance (authentication, VPN)
- Access control services (card, fingerprint) should only be available to trusted HA users

---

## Known Quirks

| Firmware | Doorbell event | Note |
|---|---|---|
| GOLIATH Hybrid | `CallNoAnswered;action=Start` | Standard doorbell events not present |
| Standard Dahua VTO | `BackKeyLight;action=Start` | Common |
| Some VTO | `RingBell;action=Pulse` | Alternative |

- **Multi-line JSON:** The device sends event data split across multiple lines – the integration buffers them correctly.
- **Button indexing:** Some firmware uses 0-based, others 1-based – both are recognised.
- **Door contact:** GOLIATH sends `DoorStatus;action=Pulse;data={"Relay":true}` when the relay fires. The sensor shows OPEN for 8 seconds since no physical contact sensor is connected.
- **Call active sensor:** On GOLIATH firmware `VideoTalk` events may only appear when a SIP client actually connects. The sensor turns OFF immediately on `CallNoAnswered`.

---

## Changelog

| Version | Changes |
|---|---|
| **1.10.0** | Fix snapshot (CallNoAnswered in image.py), door contact via Relay=true (8s auto-reset), new "Call active" binary sensor, UserID auto-resolution by name for card/fingerprint services |
| **1.9.0** | Bilingual DE/EN via HA translation system, HACS documentation, security improvements (URL sanitisation), NumberSelector BOX instead of slider |
| **1.8.0** | Multi-line JSON stream parser, DoorStatus Pulse handling |
| **1.7.0** | CallNoAnswered as doorbell event, VideoTalk in EVENT_CODES, typo fix |
| **1.6.0** | UserID→UserName mapping (user_map), sensor shows user names |
| **1.5.0** | Initial public release |

---

## License

MIT License – Copyright (c) 2026 Timo Neumeier – see [LICENSE](LICENSE)
