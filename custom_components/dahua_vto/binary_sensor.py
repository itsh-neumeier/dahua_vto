"""Binary sensor platform for DahuaVTO – doorbell, missed calls, door contact."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AVAILABILITY, SIGNAL_EVENT
from .coordinator import DahuaCoordinator
from .models import DahuaEvent

_LOGGER = logging.getLogger(__name__)

# Auto-reset delays
_DOORBELL_RESET_DELAY  = 5   # 5 seconds – visual "ringing" indicator
_MISSED_CALL_RESET_DELAY = 60  # 1 minute

# Doorbell event codes/actions (same as event.py)
# CallNoAnswered;action=Start is what GOLIATH firmware fires on button press
_DOORBELL_CODES   = {"BackKeyLight", "RingBell", "VideoTalk", "CallNoAnswered"}
_DOORBELL_ACTIONS = {"Start", "Pulse", "On", "Active"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    # One doorbell binary sensor per button (visual on/off, auto-resets after 5 s)
    for btn in range(coordinator.module_config.button_count):
        entities.append(DahuaDoorbellSensor(coordinator, entry.entry_id, btn))

    entities.append(DahuaMissedCallSensor(coordinator, entry.entry_id))
    entities.append(DahuaDoorContactSensor(coordinator, entry.entry_id))

    async_add_entities(entities)


class DahuaBaseBinarySensor(BinarySensorEntity):
    """Base class for DahuaVTO binary sensors."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Override in subclass to enable auto-reset (seconds). None = no auto-reset.
    _auto_reset_delay: int | None = None

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_available = coordinator.available
        self._attr_is_on = False
        self._reset_task: asyncio.Task | None = None

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

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending reset task when entity is removed."""
        self._cancel_reset()

    @callback
    def _handle_availability(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Auto-reset helpers
    # ------------------------------------------------------------------

    def _turn_on_with_reset(self) -> None:
        """Set sensor ON and schedule automatic reset after _auto_reset_delay."""
        self._attr_is_on = True
        self.async_write_ha_state()
        if self._auto_reset_delay is not None:
            self._schedule_reset(self._auto_reset_delay)

    def _turn_off(self) -> None:
        """Set sensor OFF and cancel any pending reset."""
        self._cancel_reset()
        if self._attr_is_on:
            self._attr_is_on = False
            self.async_write_ha_state()

    def _schedule_reset(self, delay: int) -> None:
        """Schedule a reset after `delay` seconds (cancels any existing one)."""
        self._cancel_reset()
        self._reset_task = self.hass.async_create_task(self._delayed_reset(delay))

    def _cancel_reset(self) -> None:
        if self._reset_task and not self._reset_task.done():
            self._reset_task.cancel()
        self._reset_task = None

    async def _delayed_reset(self, delay: int) -> None:
        """Turn off sensor after delay if it's still on."""
        await asyncio.sleep(delay)
        if self._attr_is_on:
            _LOGGER.debug("%s: auto-reset after %ds", self.name, delay)
            self._attr_is_on = False
            self.async_write_ha_state()


class DahuaDoorbellSensor(DahuaBaseBinarySensor):
    """Binary sensor: doorbell button pressed.

    Turns ON when the button is pressed, auto-resets after _DOORBELL_RESET_DELAY
    seconds.  Provides a classic on/off visual indicator alongside the EventEntity
    (which permanently shows the last-pressed timestamp).

    One instance per physical button.
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _auto_reset_delay = _DOORBELL_RESET_DELAY

    def __init__(
        self,
        coordinator: DahuaCoordinator,
        entry_id: str,
        button_index: int,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._button_index = button_index
        btn_num = button_index + 1
        self._attr_unique_id = f"{entry_id}_doorbell_{btn_num}"
        if coordinator.module_config.button_count == 1:
            self._attr_translation_key = "doorbell"
        else:
            self._attr_translation_key = "doorbell_button"
            self._attr_translation_placeholders = {"number": str(btn_num)}

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        if event.code not in _DOORBELL_CODES:
            return
        if event.action not in _DOORBELL_ACTIONS:
            return

        # Index matching: accept 0-based, 1-based or wildcard (-1)
        zero_match = (event.index == self._button_index)
        one_match  = (event.index == self._button_index + 1)
        wild_match = (event.index == -1)
        if not (zero_match or one_match or wild_match):
            return

        _LOGGER.info(
            "Doorbell sensor %d ON (code=%s action=%s) – resets in %ds",
            self._button_index + 1, event.code, event.action, _DOORBELL_RESET_DELAY,
        )
        self._turn_on_with_reset()


class DahuaDoorContactSensor(DahuaBaseBinarySensor):
    """Binary sensor: door contact (open / closed).

    Listens for DoorStatus, DoorOpen, DoorClose events from the device.
    If no physical door contact is connected, this sensor will simply never
    change and can be hidden in the HA UI.

    Supported event variants (different firmware versions):
      Code=DoorStatus;action=Open  / action=Close
      Code=DoorStatus;action=Start / action=Stop   (some firmware)
      Code=DoorOpen;action=Pulse
      Code=DoorClose;action=Pulse
    """

    _attr_device_class = BinarySensorDeviceClass.DOOR
    # No auto-reset: door state is explicit (Open/Close events)
    _auto_reset_delay = None

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_door_contact"
        self._attr_translation_key = "door_contact"

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        if event.code == "DoorStatus":
            if event.action in ("Open", "Start"):
                _LOGGER.info("Door contact: OPEN")
                self._turn_on_with_reset()
            elif event.action in ("Close", "Stop"):
                _LOGGER.info("Door contact: CLOSED")
                self._turn_off()
            elif event.action == "Pulse":
                # Some firmware sends action=Pulse with Status inside the JSON data,
                # e.g.:  data={"Relay":true,"Status":"Close"}
                status = event.data.get("Status", "")
                if status == "Open":
                    _LOGGER.info("Door contact: OPEN (DoorStatus/Pulse Status=Open)")
                    self._turn_on_with_reset()
                elif status == "Close":
                    _LOGGER.info("Door contact: CLOSED (DoorStatus/Pulse Status=Close)")
                    self._turn_off()
                else:
                    _LOGGER.debug("DoorStatus/Pulse with unknown Status=%r – ignored", status)
        elif event.code == "DoorOpen":
            _LOGGER.info("Door contact: OPEN (DoorOpen event)")
            self._turn_on_with_reset()
        elif event.code == "DoorClose":
            _LOGGER.info("Door contact: CLOSED (DoorClose event)")
            self._turn_off()


class DahuaMissedCallSensor(DahuaBaseBinarySensor):
    """Binary sensor: call went unanswered.

    Turns ON when a CallNoAnswered event fires.
    Turns OFF when:
    - A Stop event arrives (device resets)
    - OR automatically after _MISSED_CALL_RESET_DELAY seconds (device never sends Stop)
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _auto_reset_delay = _MISSED_CALL_RESET_DELAY

    def __init__(self, coordinator: DahuaCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_missed_call"
        self._attr_translation_key = "missed_call"

    @callback
    def _handle_event(self, event: DahuaEvent) -> None:
        if event.code != "CallNoAnswered":
            return
        if event.action == "Start":
            _LOGGER.info("Missed call detected – will auto-reset in %ds", _MISSED_CALL_RESET_DELAY)
            self._turn_on_with_reset()
        elif event.action == "Stop":
            _LOGGER.info("Missed call cleared by Stop event")
            self._turn_off()
