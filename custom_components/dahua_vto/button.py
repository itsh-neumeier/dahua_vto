"""Button platform for DahuaVTO – door unlock."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AVAILABILITY
from .coordinator import DahuaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DahuaDoorButton(coordinator, entry.entry_id)])


class DahuaDoorButton(ButtonEntity):
    """Button to open the door lock."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_open_door"
        self._attr_translation_key = "open_door"
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

    async def async_press(self) -> None:
        """Open the door."""
        _LOGGER.info("Opening door via DahuaVTO")
        success = await self._coordinator.client.open_door()
        if not success:
            _LOGGER.warning("Door open command may have failed – check device logs")
