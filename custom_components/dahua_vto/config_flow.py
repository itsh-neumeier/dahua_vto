"""Config flow for DahuaVTO integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_PORT, DEFAULT_USERNAME, DOMAIN
from .dahua_client import DahuaAuthError, DahuaClient, DahuaConnectionError
from .discovery import discover_device

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class DahuaVTOConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for DahuaVTO."""

    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}
        self._device_info = None
        self._module_config = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Unique ID: IP + Port
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            client = DahuaClient(
                host=host,
                port=port,
                username=username,
                password=password,
                session=async_get_clientsession(self.hass),
            )

            try:
                device_info, module_config = await discover_device(client)
                self._user_input = user_input
                self._device_info = device_info
                self._module_config = module_config
                return await self.async_step_confirm()

            except DahuaAuthError:
                errors["base"] = "invalid_auth"
            except DahuaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during device setup")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show discovered modules with editable fields so the user can correct them."""
        mc = self._module_config
        di = self._device_info

        if user_input is not None:
            host = self._user_input[CONF_HOST]
            title = di.model or f"DahuaVTO ({host})"
            return self.async_create_entry(
                title=title,
                data={
                    CONF_HOST: self._user_input[CONF_HOST],
                    CONF_PORT: self._user_input[CONF_PORT],
                    CONF_USERNAME: self._user_input[CONF_USERNAME],
                    CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                    "button_count": int(user_input["button_count"]),
                    "has_fingerprint": user_input["has_fingerprint"],
                    "has_card_reader": user_input["has_card_reader"],
                    "device_model": di.model,
                    "device_serial": di.serial,
                    "device_firmware": di.firmware,
                },
            )

        confirm_schema = vol.Schema(
            {
                vol.Required("button_count", default=mc.button_count): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=8,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required("has_fingerprint", default=mc.has_fingerprint): bool,
                vol.Required("has_card_reader", default=mc.has_card_reader): bool,
            }
        )

        return self.async_show_form(
            step_id="confirm",
            data_schema=confirm_schema,
            description_placeholders={
                "model": di.model or "Dahua VTO",
                "firmware": di.firmware or "–",
            },
        )
