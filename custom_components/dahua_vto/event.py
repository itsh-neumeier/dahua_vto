"""Event platform for DahuaVTO – doorbell buttons, fingerprint, card reader."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity, EventDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACCESS_METHOD_CARD,
    ACCESS_METHOD_FINGERPRINT,
    ACCESS_METHOD_FACE,
    ACCESS_METHOD_PIN,
    DOMAIN,
    SIGNAL_AVAILABILITY,
    SIGNAL_EVENT,
)
from .coordinator import DahuaCoordinator
from .models import DahuaEvent

_LOGGER = logging.getLogger(__name__)

# Event codes that represent a doorbell button press.
# Different Dahua/GOLIATH firmware versions use different codes.
DOORBELL_CODES = {
    "BackKeyLight",    # button backlight activation (most Dahua VTO)
    "RingBell",        # explicit ring-bell event
    "VideoTalk",       # video call start (some VTO firmware)
    "CallNoAnswered",  # GOLIATH firmware: fires on button press (action=Start)
}
# Doorbell actions that count as a button press
DOORBELL_ACTIONS = {"Start", "Pulse", "On", "Active"}
# Access control event code
ACCESS_CODE = "AccessControl"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up event entities for DahuaVTO."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    mc = coordinator.module_config
    di = coordinator.device_info

    entities: list[EventEntity] = []

    # One Event entity per doorbell button
    for btn in range(mc.button_count):
        entities.append(
            DahuaButtonEvent(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                button_index=btn,
            )
        )

    # Fingerprint module
    if mc.has_fingerprint:
        entities.append(
            DahuaFingerprintEvent(
                coordinator=coordinator,
                entry_id=entry.entry_id,
            )
        )

    # Card reader
    if mc.has_card_reader:
        entities.append(
            DahuaCardEvent(
                coordinator=coordinator,
                entry_id=entry.entry_id,
            )
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class DahuaBaseEvent(EventEntity):
    """Base class for DahuaVTO event entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_available = coordinator.available

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
        """Subscribe to dispatcher signals when added to HA."""
        # Call super() so EventEntity (RestoreEntity) restores previous state
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_EVENT}_{self._entry_id}",
                self._handle_event_signal,
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
    def _handle_event_signal(self, event: DahuaEvent) -> None:
        """Override in subclass to filter and process events."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Doorbell button
# ---------------------------------------------------------------------------

class DahuaButtonEvent(DahuaBaseEvent):
    """Event entity for a single doorbell button."""

    _attr_event_types = ["pressed"]
    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(
        self,
        coordinator: DahuaCoordinator,
        entry_id: str,
        button_index: int,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._button_index = button_index
        btn_num = button_index + 1
        self._attr_unique_id = f"{entry_id}_button_{btn_num}"
        if coordinator.module_config.button_count == 1:
            self._attr_translation_key = "doorbell"
        else:
            self._attr_translation_key = "doorbell_button"
            self._attr_translation_placeholders = {"number": str(btn_num)}

    @callback
    def _handle_event_signal(self, event: DahuaEvent) -> None:
        """Fire when our button index matches the incoming event."""
        if event.code not in DOORBELL_CODES:
            return
        if event.action not in DOORBELL_ACTIONS:
            return

        # index matching:
        #   -1  → single-button device or device that omits the index field
        #    0  → button 1 (0-based)  → matches button_index 0
        #    1  → button 2 (0-based)  → matches button_index 1
        # Some devices use 1-based indexing (index=1 means button 1).
        # We accept both: index==button_index (0-based) OR index==button_index+1 (1-based)
        zero_match = (event.index == self._button_index)
        one_match  = (event.index == self._button_index + 1)
        wild_match = (event.index == -1)
        if not (zero_match or one_match or wild_match):
            return

        _LOGGER.info(
            "Button %d pressed (code=%s action=%s raw_index=%d)",
            self._button_index + 1, event.code, event.action, event.index,
        )
        self._trigger_event(
            "pressed",
            {
                "button": self._button_index + 1,
                "raw_index": event.index,
                "event_code": event.code,
            },
        )
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Fingerprint scanner
# ---------------------------------------------------------------------------

class DahuaFingerprintEvent(DahuaBaseEvent):
    """Event entity for fingerprint scan results."""

    _attr_event_types = ["granted", "denied"]
    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_fingerprint"
        self._attr_translation_key = "fingerprint"

    @callback
    def _handle_event_signal(self, event: DahuaEvent) -> None:
        if event.code != ACCESS_CODE:
            return
        method = event.access_method
        if method not in (ACCESS_METHOD_FINGERPRINT,):
            return

        # Dahua typically sets ErrorCode=0 for success
        granted = event.data.get("ErrorCode", 0) == 0
        event_type = "granted" if granted else "denied"

        _LOGGER.debug("Fingerprint %s for user %s", event_type, event.user_id)
        self._trigger_event(
            event_type,
            {
                "user_id": event.user_id,
                "method": method,
            },
        )
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Card / PIN reader
# ---------------------------------------------------------------------------

class DahuaCardEvent(DahuaBaseEvent):
    """Event entity for card / PIN access events."""

    _attr_event_types = ["granted", "denied"]
    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_card"
        self._attr_translation_key = "card_access"

    @callback
    def _handle_event_signal(self, event: DahuaEvent) -> None:
        if event.code != ACCESS_CODE:
            return
        method = event.access_method
        if method not in (ACCESS_METHOD_CARD, ACCESS_METHOD_PIN, ACCESS_METHOD_FACE):
            return

        granted = event.data.get("ErrorCode", 0) == 0
        event_type = "granted" if granted else "denied"

        method_name = {
            ACCESS_METHOD_CARD: "card",
            ACCESS_METHOD_PIN: "pin",
            ACCESS_METHOD_FACE: "face",
        }.get(method, "unknown")

        _LOGGER.debug("Card access %s (method=%s)", event_type, method_name)
        self._trigger_event(
            event_type,
            {
                "user_id": event.user_id,
                "card_no": event.card_no,
                "method": method_name,
            },
        )
        self.async_write_ha_state()
