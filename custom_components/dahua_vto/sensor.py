"""Sensor platform for DahuaVTO – last door access."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
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

_METHOD_NAMES: dict[int, str] = {
    ACCESS_METHOD_CARD: "Karte",
    ACCESS_METHOD_PIN: "PIN",
    ACCESS_METHOD_FINGERPRINT: "Fingerabdruck",
    ACCESS_METHOD_FACE: "Gesicht",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DahuaLastAccessSensor(coordinator, entry.entry_id)])


class DahuaLastAccessSensor(SensorEntity):
    """Sensor: last access event (card / fingerprint / PIN / face).

    State  : user ID (or "Unbekannt")
    Attrs  : method, granted, timestamp, card_no (if applicable)
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:account-key"

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_last_access"
        self._attr_translation_key = "last_access"
        self._attr_available = coordinator.available
        self._attr_native_value: str | None = None
        self._extra: dict[str, Any] = {}

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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra

    async def async_added_to_hass(self) -> None:
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
        if event.code != "AccessControl":
            return

        granted = event.data.get("ErrorCode", 0) == 0
        method_num = event.access_method
        method_name = _METHOD_NAMES.get(method_num, f"Methode {method_num}")
        user_id = str(event.user_id or event.data.get("UserID", "")).strip()
        card_no = event.card_no or event.data.get("CardNo", "")

        # Look up the human-readable name from the coordinator's user map
        user_name = self._coordinator.user_map.get(user_id, "")

        # State: show name if known, fall back to ID, then "Unbekannt"
        self._attr_native_value = user_name or user_id or "Unbekannt"
        self._extra = {
            "user_id": user_id,
            "user_name": user_name or user_id,
            "method": method_name,
            "granted": granted,
            "timestamp": dt_util.now().isoformat(),
        }
        if card_no:
            self._extra["card_no"] = card_no

        _LOGGER.info(
            "DahuaVTO: last access user=%s (%s) method=%s granted=%s",
            user_id, user_name or "?", method_name, granted,
        )
        self.async_write_ha_state()
