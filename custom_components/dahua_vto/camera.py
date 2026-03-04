"""Camera platform for DahuaVTO – RTSP video stream."""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RTSP_PORT, SIGNAL_AVAILABILITY
from .coordinator import DahuaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up camera entity."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [DahuaCamera(coordinator, entry.entry_id, entry.data)]
    )


class DahuaCamera(Camera):
    """Camera entity providing the RTSP stream of the VTO door station."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_camera"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: DahuaCoordinator,
        entry_id: str,
        config_data: dict,
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._config = config_data
        self._attr_unique_id = f"{entry_id}_camera"
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

    async def stream_source(self) -> str | None:
        """Return the RTSP stream URL (main stream, channel 1)."""
        host = self._config[CONF_HOST]
        username = self._config[CONF_USERNAME]
        password = self._config[CONF_PASSWORD]
        return (
            f"rtsp://{username}:{password}@{host}:{RTSP_PORT}"
            f"/cam/realmonitor?channel=1&subtype=0"
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a snapshot from the device's snapshot CGI."""
        try:
            return await self._coordinator.client.get_snapshot()
        except Exception as exc:
            _LOGGER.warning("Could not fetch snapshot: %s", exc)
        return None
