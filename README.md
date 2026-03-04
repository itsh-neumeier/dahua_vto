# DahuaVTO – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.11.0-brightgreen.svg)](https://github.com/itsh-neumeier/dahua_vto/releases)

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
| `lock` | Door lock | Always (unlock triggers relay, 5 s auto-reset) |
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
| `dahua_vto.call_room` | Initiate a video call to a VTH indoor unit by room number |
| `dahua_vto.stop_call` | Hang up an active call |
| `dahua_vto.trigger_alarm` | Switch alarm output on/off (with optional auto-stop duration) |
| `dahua_vto.get_logs` | Fetch recent events (fires `dahua_vto_logs_fetched`) |

> **Tip:** For `learn_card` and `enroll_fingerprint`, providing only `user_name` is sufficient. The integration automatically finds the matching UserID or generates a new one.

### HA Bus Events (for automations)

| Event | When |
|---|---|
| `dahua_vto_card_learned` | Card detected in learning mode |
| `dahua_vto_fingerprint_enrolled` | Fingerprint enrollment successful |
| `dahua_vto_fingerprint_failed` | Enrollment failed / timed out |
| `dahua_vto_doorbell_snapshot` | Snapshot ready after doorbell press |
| `dahua_vto_users_listed` | User list fetched |
| `dahua_vto_logs_fetched` | Log entries returned by `get_logs` service |

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

> **Note:** Entity IDs depend on your device model. Replace `av_vta05_22av2` with your actual device name as shown in HA. Replace `notify.mobile_app_my_phone` with your actual notification service.

---

### 🔔 Doorbell

#### Push notification with snapshot photo

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

#### Flash indoor lights on doorbell

```yaml
automation:
  - alias: "Doorbell – Flash indoor lights"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room
        data:
          flash: short
```

#### TTS announcement on doorbell

```yaml
automation:
  - alias: "Doorbell – TTS announcement"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
        to: "on"
    action:
      - service: tts.speak
        target:
          entity_id: media_player.echo_dot
        data:
          message: "Someone is at the front door!"
```

#### Turn on outdoor light at night on ring

```yaml
automation:
  - alias: "Doorbell – Outdoor light at night"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
        to: "on"
    condition:
      - condition: sun
        after: sunset
        before: sunrise
    action:
      - service: light.turn_on
        target:
          entity_id: light.outdoor_entrance
      - delay: "00:05:00"
      - service: light.turn_off
        target:
          entity_id: light.outdoor_entrance
```

#### Record a 30-second camera clip on ring

```yaml
automation:
  - alias: "Doorbell – Record 30 s camera clip"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
        to: "on"
    action:
      - service: camera.record
        target:
          entity_id: camera.av_vta05_22av2_door_camera
        data:
          filename: "/config/www/recordings/doorbell_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4"
          duration: 30
```

#### Auto-call indoor station on ring (ring-through)

```yaml
automation:
  - alias: "Doorbell – Auto-call indoor station"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
        to: "on"
    action:
      - service: dahua_vto.call_room
        data:
          room_no: "8020"
```

---

### 🔑 Access Control

#### Open door on granted fingerprint

```yaml
automation:
  - alias: "Fingerprint – Open door (granted)"
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

#### Notification on access with user name

```yaml
automation:
  - alias: "Access – Notification with user name"
    trigger:
      - platform: state
        entity_id: sensor.av_vta05_22av2_last_access
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "Door accessed"
          message: >
            {{ states('sensor.av_vta05_22av2_last_access') }}
            entered via {{ state_attr('sensor.av_vta05_22av2_last_access', 'method') }}
            at {{ now().strftime('%H:%M') }}.
```

#### Alert on denied access attempt

```yaml
automation:
  - alias: "Access – Denied access alert"
    trigger:
      - platform: state
        entity_id: event.av_vta05_22av2_card_access
        attribute: event_type
        to: "denied"
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "⚠️ Access denied"
          message: "An unknown card or fingerprint was rejected at the door."
```

#### Auto-open door for a specific person

```yaml
automation:
  - alias: "Access – Auto-open door for Timo"
    trigger:
      - platform: state
        entity_id: sensor.av_vta05_22av2_last_access
    condition:
      - condition: template
        value_template: >
          {{ state_attr('sensor.av_vta05_22av2_last_access', 'user_name') == 'TimoNeumeier' }}
    action:
      - service: lock.unlock
        target:
          entity_id: lock.av_vta05_22av2_door_lock
```

#### Register new card by name only

```yaml
# Run as a service call – no entity_id needed
service: dahua_vto.learn_card
data:
  user_name: "John Doe"
  timeout: 30
# The integration auto-generates a UserID if "John Doe" is not yet registered.
```

---

### 📞 Calls

#### Missed call notification

```yaml
automation:
  - alias: "Call – Missed call notification"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_missed_call
        to: "on"
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "📞 Missed call at the door"
          message: "Someone rang the doorbell and no one answered."
```

#### Lower TV volume while call is active

```yaml
automation:
  - alias: "Call – Lower TV volume when call active"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_call_active
        to: "on"
    action:
      - service: media_player.volume_set
        target:
          entity_id: media_player.living_room_tv
        data:
          volume_level: 0.1

  - alias: "Call – Restore TV volume when call ends"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_call_active
        to: "off"
    action:
      - service: media_player.volume_set
        target:
          entity_id: media_player.living_room_tv
        data:
          volume_level: 0.5
```

#### Auto-hang up after 60 seconds

```yaml
automation:
  - alias: "Call – Auto-hang up after 60 s"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_call_active
        to: "on"
        for: "00:01:00"
    action:
      - service: dahua_vto.stop_call
```

#### Call indoor station as announcement (e.g. package delivery)

```yaml
script:
  announce_package_delivery:
    alias: "Announce: Package at door"
    sequence:
      - service: dahua_vto.call_room
        data:
          room_no: "8020"
```

---

### 🚨 Alarm

#### Trigger alarm on night-time doorbell

```yaml
automation:
  - alias: "Security – Alarm on doorbell at night"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_doorbell
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
          duration: 30        # auto-stop after 30 seconds
      - service: notify.mobile_app_my_phone
        data:
          title: "🚨 Night alarm!"
          message: "Someone rang the doorbell at night – alarm triggered."
```

#### Stop alarm via script / button

```yaml
script:
  stop_door_alarm:
    alias: "Stop door alarm"
    sequence:
      - service: dahua_vto.trigger_alarm
        data:
          channel: 1
          active: false
```

---

### 🚪 Door Contact

#### Notify when door is open longer than 5 minutes

```yaml
automation:
  - alias: "Door – Alert if open more than 5 minutes"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_door_contact
        to: "on"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "🚪 Door still open"
          message: "The front door has been open for more than 5 minutes!"
```

#### Save snapshot when door relay fires

```yaml
automation:
  - alias: "Door – Snapshot on door open"
    trigger:
      - platform: state
        entity_id: binary_sensor.av_vta05_22av2_door_contact
        to: "on"
    action:
      - service: camera.snapshot
        target:
          entity_id: camera.av_vta05_22av2_door_camera
        data:
          filename: "/config/www/snapshots/door_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg"
```

---

### 🛠️ Management

#### Notification when card learning completes

```yaml
automation:
  - alias: "Management – Card learned notification"
    trigger:
      - platform: event
        event_type: dahua_vto_card_learned
    action:
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.status == 'learned' }}"
            sequence:
              - service: notify.mobile_app_my_phone
                data:
                  title: "✅ Card registered"
                  message: >
                    Card {{ trigger.event.data.card_no }}
                    assigned to user {{ trigger.event.data.user_id }}.
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.status == 'timeout' }}"
            sequence:
              - service: notify.mobile_app_my_phone
                data:
                  title: "⏱️ Card learning timed out"
                  message: "No card was presented within the time limit."
```

#### Notification on fingerprint enrollment success or failure

```yaml
automation:
  - alias: "Management – Fingerprint enrolled"
    trigger:
      - platform: event
        event_type: dahua_vto_fingerprint_enrolled
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "✅ Fingerprint enrolled"
          message: >
            Fingerprint for {{ trigger.event.data.user_name }}
            (finger {{ trigger.event.data.finger_index }}) registered successfully.

  - alias: "Management – Fingerprint enrollment failed"
    trigger:
      - platform: event
        event_type: dahua_vto_fingerprint_failed
    action:
      - service: notify.mobile_app_my_phone
        data:
          title: "❌ Fingerprint enrollment failed"
          message: >
            Enrollment for user {{ trigger.event.data.user_id }}
            failed: {{ trigger.event.data.reason }}.
```

#### Load user list on HA startup

```yaml
automation:
  - alias: "Management – Load users on HA start"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - delay: "00:00:30"    # wait for integration to connect
      - service: dahua_vto.list_users
```

---

### 📋 Logs

#### Fetch access log every hour

```yaml
automation:
  - alias: "Log – Fetch access log hourly"
    trigger:
      - platform: time_pattern
        hours: "/1"
    action:
      - service: dahua_vto.get_logs
        data:
          count: 50
          source: memory    # always available; use "both" to also query the device
```

#### Persistent notification with latest log entry

```yaml
automation:
  - alias: "Log – Show latest entry after fetch"
    trigger:
      - platform: event
        event_type: dahua_vto_logs_fetched
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.count > 0 }}"
    action:
      - service: notify.persistent_notification
        data:
          title: "DahuaVTO – Access Log ({{ trigger.event.data.count }} entries)"
          message: >
            {% set last = trigger.event.data.records | last %}
            Last: {{ last.timestamp }}
            – {{ last.code }}/{{ last.action }}
            {% if last.user_name %} ({{ last.user_name }}){% endif %}
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
| **1.11.0** | New lock entity (door relay as HA lock, 5 s auto-reset), new services: `call_room`, `stop_call`, `trigger_alarm` (with auto-stop duration), `get_logs` (in-memory + RPC2 log, fires `dahua_vto_logs_fetched`) |
| **1.10.0** | Fix snapshot (CallNoAnswered in image.py), door contact via Relay=true (8s auto-reset), new "Call active" binary sensor, UserID auto-resolution by name for card/fingerprint services |
| **1.9.0** | Bilingual DE/EN via HA translation system, HACS documentation, security improvements (URL sanitisation), NumberSelector BOX instead of slider |
| **1.8.0** | Multi-line JSON stream parser, DoorStatus Pulse handling |
| **1.7.0** | CallNoAnswered as doorbell event, VideoTalk in EVENT_CODES, typo fix |
| **1.6.0** | UserID→UserName mapping (user_map), sensor shows user names |
| **1.5.0** | Initial public release |

---

## License

MIT License – Copyright (c) 2026 Timo Neumeier – see [LICENSE](LICENSE)
