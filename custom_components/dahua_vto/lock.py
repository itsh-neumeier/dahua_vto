"""Lock entity for DahuaVTO – controls the door relay as a HA lock."""
from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AVAILABILITY
from .coordinator import DahuaCoordinator

_LOGGER = logging.getLogger(__name__)

# Seconds after which the lock entity automatically resets to "locked".
# The physical relay is momentary – the lock state in HA must mirror this.
_UNLOCK_RESET_DELAY = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DahuaVTO lock entity."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DahuaDoorLock(coordinator, entry.entry_id)])


class DahuaDoorLock(LockEntity):
    """Represents the door relay as a Home Assistant lock.

    Unlocking triggers the door relay (same as pressing the "Open door" button).
    The lock auto-resets to LOCKED after _UNLOCK_RESET_DELAY seconds because
    the hardware relay is momentary – it does not hold the door open permanently.

    Supports LockEntityFeature.OPEN so the HA dashboard shows an "Open" button
    alongside the usual Lock / Unlock actions.
    """

    _attr_should_poll = False
    _attr_translation_key = "door_lock"
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        di = coordinator.device_info
        self._attr_unique_id = f"{entry_id}_door_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=di.model or "DahuaVTO",
            model=di.model,
            manufacturer="Dahua / GOLIATH",
            sw_version=di.firmware,
            configuration_url=f"http://{di.ip}",
        )
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._reset_handle = None

    # ------------------------------------------------------------------
    # HA lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to device availability updates."""
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

    @property
    def available(self) -> bool:
        return self._coordinator.available

    # ------------------------------------------------------------------
    # Lock actions
    # ------------------------------------------------------------------

    async def async_unlock(self, **kwargs) -> None:
        """Trigger the door relay (unlock)."""
        # Cancel any pending auto-reset
        if self._reset_handle is not None:
            self._reset_handle.cancel()
            self._reset_handle = None

        self._attr_is_unlocking = True
        self._attr_is_locked = False
        self.async_write_ha_state()

        success = await self._coordinator.client.open_door()
        self._attr_is_unlocking = False

        if success:
            _LOGGER.debug("DahuaDoorLock: relay triggered – door unlocked")
        else:
            _LOGGER.warning("DahuaDoorLock: open_door returned failure")

        # Schedule auto-reset back to locked
        self._reset_handle = self.hass.loop.call_later(
            _UNLOCK_RESET_DELAY, self._reset_to_locked
        )
        self.async_write_ha_state()

    async def async_lock(self, **kwargs) -> None:
        """Reset lock state to LOCKED (the physical relay already returned)."""
        if self._reset_handle is not None:
            self._reset_handle.cancel()
            self._reset_handle = None
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self.async_write_ha_state()

    async def async_open(self, **kwargs) -> None:
        """HA 'open' action (LockEntityFeature.OPEN) – same as unlock."""
        await self.async_unlock(**kwargs)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @callback
    def _reset_to_locked(self) -> None:
        """Automatically return to LOCKED state after the relay pulse."""
        self._reset_handle = None
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self.async_write_ha_state()
