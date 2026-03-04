"""Module auto-discovery for DahuaVTO integration."""
from __future__ import annotations

import logging

from .dahua_client import DahuaClient
from .models import DeviceInfo, ModuleConfig

_LOGGER = logging.getLogger(__name__)


async def discover_device(
    client: DahuaClient,
) -> tuple[DeviceInfo, ModuleConfig]:
    """
    Query the device and return DeviceInfo + ModuleConfig.

    Called during config flow setup and when the config entry is loaded.
    """
    _LOGGER.debug("Starting device discovery")

    device_info = await client.get_system_info()
    _LOGGER.debug(
        "Device: model=%s serial=%s firmware=%s",
        device_info.model,
        device_info.serial,
        device_info.firmware,
    )

    module_config = await client.get_module_config()
    _LOGGER.debug(
        "Modules: buttons=%d fingerprint=%s card=%s",
        module_config.button_count,
        module_config.has_fingerprint,
        module_config.has_card_reader,
    )

    return device_info, module_config
