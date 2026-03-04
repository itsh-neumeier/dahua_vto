"""Image platform for DahuaVTO – live snapshots.

Two image entities:
- DahuaSnapshotImage   : auto-updates on doorbell press (fires dahua_vto_doorbell_snapshot)
- DahuaAccessSnapshot  : auto-updates on card / fingerprint / face access
                         (fires dahua_vto_access_snapshot with user info)
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ACCESS_METHOD_CARD,
    ACCESS_METHOD_FACE,
    ACCESS_METHOD_FINGERPRINT,
    ACCESS_METHOD_PIN,
    DOMAIN,
    SIGNAL_AVAILABILITY,
    SIGNAL_EVENT,
)
from .coordinator import DahuaCoordinator
from .models import DahuaEvent

_LOGGER = logging.getLogger(__name__)

# Same codes/actions as event.py / binary_sensor.py doorbell detection.
# CallNoAnswered is the GOLIATH firmware event for a button press.
_DOORBELL_CODES = {"BackKeyLight", "RingBell", "VideoTalk", "CallNoAnswered"}
_DOORBELL_ACTIONS = {"Start", "Pulse", "On", "Active"}

# Human-readable method names for the bus event payload
_METHOD_NAMES: dict[int, str] = {
    ACCESS_METHOD_CARD: "card",
    ACCESS_METHOD_PIN: "pin",
    ACCESS_METHOD_FINGERPRINT: "fingerprint",
    ACCESS_METHOD_FACE: "face",
}

# HA bus events fired by this module
EVENT_DOORBELL_SNAPSHOT = "dahua_vto_doorbell_snapshot"
EVENT_ACCESS_SNAPSHOT = "dahua_vto_access_snapshot"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image entities."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DahuaSnapshotImage(coordinator, entry.entry_id),
        DahuaAccessSnapshot(coordinator, entry.entry_id),
    ])


class DahuaSnapshotImage(ImageEntity):
    """Image entity: JPEG snapshot from the doorbell camera.

    - Auto-fetches a new snapshot whenever the doorbell is pressed.
    - After fetching, fires the HA bus event 'dahua_vto_doorbell_snapshot'
      with the entity_id so automations can use it in mobile notifications:

      trigger:
        platform: event
        event_type: dahua_vto_doorbell_snapshot
      action:
        service: notify.mobile_app_phone
        data:
          message: "Klingel!"
          data:
            image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
    """

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        # ImageEntity.__init__(hass) MUST be called first – it initialises
        # access_tokens, _cached_image and other internal state that HA
        # needs before the entity can write state.
        super().__init__(coordinator.hass)
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_snapshot"
        self._attr_translation_key = "door_snapshot"
        self._attr_available = coordinator.available
        self._image_bytes: bytes | None = None
        self._attr_image_last_updated: datetime | None = None

    @property
    def device_info(self) -> HADeviceInfo:
        di = self._coordinator.device_info
        return HADeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=di.model or "DahuaVTO",
            model=di.model,
            sw_version=di.firmware,
            manufacturer="Dahua / GOLIATH",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to dispatcher signals."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENT}_{self._entry_id}",
                self._handle_event,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_AVAILABILITY}_{self._entry_id}",
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        """On doorbell ring → schedule an async snapshot fetch."""
        if event.code not in _DOORBELL_CODES:
            return
        if event.action not in _DOORBELL_ACTIONS:
            return
        self.hass.async_create_task(self._fetch_snapshot(event.index))

    async def _fetch_snapshot(self, button_index: int) -> None:
        """Fetch a JPEG snapshot, update the entity, fire the bus event."""
        try:
            image_bytes = await self._coordinator.client.get_snapshot()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("DahuaVTO: snapshot fetch failed: %s", exc)
            return

        if not image_bytes:
            _LOGGER.warning("DahuaVTO: snapshot returned empty response")
            return

        self._image_bytes = image_bytes
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

        _LOGGER.info(
            "DahuaVTO: snapshot updated (%d bytes) after doorbell press (button %d)",
            len(image_bytes),
            button_index + 1,
        )

        # Fire HA bus event so automations can reference the image entity
        self.hass.bus.async_fire(
            EVENT_DOORBELL_SNAPSHOT,
            {
                "entry_id": self._entry_id,
                "button": button_index + 1,
                "entity_id": self.entity_id,
                "timestamp": self._attr_image_last_updated.isoformat(),
            },
        )

    async def async_image(self) -> bytes | None:
        """Return the latest stored snapshot bytes."""
        return self._image_bytes


class DahuaAccessSnapshot(ImageEntity):
    """Image entity: JPEG snapshot taken automatically when door access is granted.

    Triggers on ``AccessControl`` events where ``ErrorCode == 0`` (access
    granted).  Works for card, fingerprint, PIN and face access methods.

    After fetching, fires the HA bus event ``dahua_vto_access_snapshot`` with
    ``entity_id``, ``user_id``, ``user_name``, ``card_no``, ``method`` and
    ``timestamp`` so automations can push a photo notification:

    .. code-block:: yaml

      trigger:
        - trigger: event
          event_type: dahua_vto_access_snapshot
      action:
        - action: notify.mobile_app_phone
          data:
            title: "Zugang: {{ trigger.event.data.user_name }}"
            message: "Methode: {{ trigger.event.data.method }}"
            data:
              image: "/api/image_proxy/{{ trigger.event.data.entity_id }}"
    """

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator.hass)
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_access_snapshot"
        self._attr_translation_key = "access_snapshot"
        self._attr_available = coordinator.available
        self._image_bytes: bytes | None = None
        self._attr_image_last_updated: datetime | None = None

    @property
    def device_info(self) -> HADeviceInfo:
        di = self._coordinator.device_info
        return HADeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=di.model or "DahuaVTO",
            model=di.model,
            sw_version=di.firmware,
            manufacturer="Dahua / GOLIATH",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to dispatcher signals."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENT}_{self._entry_id}",
                self._handle_event,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_AVAILABILITY}_{self._entry_id}",
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        """On granted AccessControl event → schedule an async snapshot fetch."""
        if event.code != "AccessControl":
            return
        # Only trigger when access was actually granted (relay fired)
        granted = event.data.get("ErrorCode", 0) == 0
        if not granted:
            return
        user_id = str(event.user_id or event.data.get("UserID", "")).strip()
        user_name = self._coordinator.user_map.get(user_id, "")
        card_no = event.card_no or event.data.get("CardNo", "")
        method = event.access_method
        self.hass.async_create_task(
            self._fetch_snapshot(user_id, user_name, card_no, method)
        )

    async def _fetch_snapshot(
        self, user_id: str, user_name: str, card_no: str, method: int
    ) -> None:
        """Fetch a JPEG snapshot, update the entity, fire the bus event."""
        try:
            image_bytes = await self._coordinator.client.get_snapshot()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("DahuaVTO: access snapshot fetch failed: %s", exc)
            return

        if not image_bytes:
            _LOGGER.warning("DahuaVTO: access snapshot returned empty response")
            return

        self._image_bytes = image_bytes
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

        method_name = _METHOD_NAMES.get(method, f"method_{method}")
        _LOGGER.info(
            "DahuaVTO: access snapshot updated (%d bytes) user=%s method=%s",
            len(image_bytes), user_id or "?", method_name,
        )

        self.hass.bus.async_fire(
            EVENT_ACCESS_SNAPSHOT,
            {
                "entry_id": self._entry_id,
                "entity_id": self.entity_id,
                "user_id": user_id,
                "user_name": user_name or user_id,
                "card_no": card_no,
                "method": method_name,
                "timestamp": self._attr_image_last_updated.isoformat(),
            },
        )

    async def async_image(self) -> bytes | None:
        """Return the latest stored snapshot bytes."""
        return self._image_bytes
