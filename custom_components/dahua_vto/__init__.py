"""DahuaVTO Home Assistant Integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PLATFORMS
from .coordinator import DahuaCoordinator
from .dahua_client import DahuaClient
from .models import DeviceInfo, ModuleConfig

_LOGGER = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Service names                                                        #
# ------------------------------------------------------------------ #
SERVICE_LEARN_CARD = "learn_card"
SERVICE_STOP_LEARN_CARD = "stop_learn_card"
SERVICE_ADD_CARD = "add_card"
SERVICE_DELETE_CARD = "delete_card"
SERVICE_ENROLL_FINGERPRINT = "enroll_fingerprint"
SERVICE_CANCEL_ENROLLMENT = "cancel_enrollment"
SERVICE_LIST_USERS = "list_users"
SERVICE_CALL_ROOM = "call_room"
SERVICE_STOP_CALL = "stop_call"
SERVICE_TRIGGER_ALARM = "trigger_alarm"
SERVICE_GET_LOGS = "get_logs"

# ------------------------------------------------------------------ #
# Service schemas                                                      #
# ------------------------------------------------------------------ #
_ENTRY_ID = vol.Optional("entry_id")

SCHEMA_LEARN_CARD = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Optional("user_id"): cv.string,
        vol.Optional("user_name"): cv.string,
        vol.Optional("timeout", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=120)
        ),
    }
)

SCHEMA_ADD_CARD = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Required("card_no"): cv.string,
        vol.Required("user_id"): cv.string,
        vol.Optional("user_name"): cv.string,
    }
)

SCHEMA_DELETE_CARD = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Required("card_no"): cv.string,
    }
)

SCHEMA_ENROLL_FINGERPRINT = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Required("user_id"): cv.string,
        vol.Optional("user_name", default=""): cv.string,
        vol.Optional("finger_index", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=9)
        ),
        vol.Optional("timeout", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=20, max=180)
        ),
    }
)

SCHEMA_ENTRY_ONLY = vol.Schema({_ENTRY_ID: cv.string})

SCHEMA_CALL_ROOM = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Required("room_no"): cv.string,
    }
)

SCHEMA_STOP_CALL = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Optional("room_no", default=""): cv.string,
    }
)

SCHEMA_TRIGGER_ALARM = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Optional("channel", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=8)
        ),
        vol.Optional("active", default=True): cv.boolean,
        vol.Optional("duration", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=3600)
        ),
    }
)

SCHEMA_GET_LOGS = vol.Schema(
    {
        _ENTRY_ID: cv.string,
        vol.Optional("count", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("source", default="both"): vol.In(["device", "memory", "both"]),
        vol.Optional("log_type", default="access"): vol.In(["access", "call", "all"]),
    }
)


def _get_coordinator(hass: HomeAssistant, call: ServiceCall) -> DahuaCoordinator:
    """Return the coordinator for the given call.

    If call.data contains 'entry_id', use that coordinator.
    Otherwise return the first (and usually only) coordinator.
    """
    entry_id = call.data.get("entry_id")
    coordinators: dict[str, DahuaCoordinator] = hass.data.get(DOMAIN, {})

    if entry_id:
        coord = coordinators.get(entry_id)
        if coord is None:
            raise ValueError(f"No DahuaVTO entry with id '{entry_id}' found")
        return coord

    if not coordinators:
        raise ValueError("No DahuaVTO device configured")

    return next(iter(coordinators.values()))


# ------------------------------------------------------------------ #
# Setup / unload                                                       #
# ------------------------------------------------------------------ #

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DahuaVTO from a config entry."""
    data = entry.data

    client = DahuaClient(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
    )

    device_info = DeviceInfo(
        model=data.get("device_model", ""),
        serial=data.get("device_serial", ""),
        firmware=data.get("device_firmware", ""),
        ip=data[CONF_HOST],
    )

    module_config = ModuleConfig(
        button_count=data.get("button_count", 1),
        has_fingerprint=data.get("has_fingerprint", False),
        has_card_reader=data.get("has_card_reader", False),
    )

    coordinator = DahuaCoordinator(
        hass=hass,
        client=client,
        device_info=device_info,
        module_config=module_config,
        entry_id=entry.entry_id,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the event stream AFTER platforms are set up so entities can subscribe
    await coordinator.async_start()

    # Register services once (first entry sets them up; subsequent entries reuse)
    if not hass.services.has_service(DOMAIN, SERVICE_LEARN_CARD):
        _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: DahuaCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    # Remove services when the last entry is unloaded
    if not hass.data.get(DOMAIN):
        for svc in (
            SERVICE_LEARN_CARD,
            SERVICE_STOP_LEARN_CARD,
            SERVICE_ADD_CARD,
            SERVICE_DELETE_CARD,
            SERVICE_ENROLL_FINGERPRINT,
            SERVICE_CANCEL_ENROLLMENT,
            SERVICE_LIST_USERS,
            SERVICE_CALL_ROOM,
            SERVICE_STOP_CALL,
            SERVICE_TRIGGER_ALARM,
            SERVICE_GET_LOGS,
        ):
            hass.services.async_remove(DOMAIN, svc)

    return unloaded


# ------------------------------------------------------------------ #
# Service handlers                                                     #
# ------------------------------------------------------------------ #

def _register_services(hass: HomeAssistant) -> None:
    """Register all DahuaVTO services."""

    async def handle_learn_card(call: ServiceCall) -> None:
        """Start card learning mode – wait for the next card scan."""
        coord = _get_coordinator(hass, call)
        await coord.start_card_learning(
            user_id=call.data.get("user_id"),
            user_name=call.data.get("user_name"),
            timeout=call.data.get("timeout", 30),
        )

    async def handle_stop_learn_card(call: ServiceCall) -> None:
        """Cancel active card learning mode."""
        coord = _get_coordinator(hass, call)
        coord.stop_card_learning()

    async def handle_add_card(call: ServiceCall) -> None:
        """Directly register a card number for a user (no scanning needed)."""
        coord = _get_coordinator(hass, call)
        user_id = call.data["user_id"]
        card_no = call.data["card_no"]
        user_name = call.data.get("user_name", "")

        if user_name:
            await coord.client.add_user(user_id, user_name)

        ok = await coord.client.add_card(card_no, user_id)
        if ok:
            _LOGGER.info("DahuaVTO: Card %s added for user %s", card_no, user_id)
        else:
            _LOGGER.warning(
                "DahuaVTO: add_card %s → user %s may have failed", card_no, user_id
            )

    async def handle_delete_card(call: ServiceCall) -> None:
        """Remove an RFID/IC card from the device."""
        coord = _get_coordinator(hass, call)
        card_no = call.data["card_no"]
        ok = await coord.client.delete_card(card_no)
        if ok:
            _LOGGER.info("DahuaVTO: Card %s deleted", card_no)
        else:
            _LOGGER.warning("DahuaVTO: delete_card %s may have failed", card_no)

    async def handle_enroll_fingerprint(call: ServiceCall) -> None:
        """Start fingerprint enrollment on the device.

        The device will prompt the user to place their finger ~3 times.
        A 'dahua_vto_fingerprint_enrolled' or 'dahua_vto_fingerprint_failed'
        event is fired on the HA bus when the process completes.
        """
        coord = _get_coordinator(hass, call)
        await coord.start_fingerprint_enrollment(
            user_id=call.data["user_id"],
            user_name=call.data.get("user_name", ""),
            finger_index=call.data.get("finger_index", 0),
            timeout=call.data.get("timeout", 60),
        )

    async def handle_cancel_enrollment(call: ServiceCall) -> None:
        """Cancel ongoing fingerprint enrollment."""
        coord = _get_coordinator(hass, call)
        if coord._fp_enroll_task and not coord._fp_enroll_task.done():
            coord._fp_enroll_task.cancel()
        await coord.client.stop_fingerprint_enrollment()

    async def handle_list_users(call: ServiceCall) -> None:
        """Fetch all registered users from the device and fire a HA bus event.

        Also refreshes the coordinator's user_map so the LastAccess sensor
        immediately shows names for subsequent access events.

        Fires 'dahua_vto_users_listed' with:
          entry_id  – config entry id
          users     – list of user dicts (UserID, UserName, etc.)
          count     – number of users
        """
        coord = _get_coordinator(hass, call)
        # Refresh the map (updates coord.user_map for sensor name lookups)
        await coord._refresh_user_map()
        users = await coord.client.get_users()
        hass.bus.async_fire(
            "dahua_vto_users_listed",
            {
                "entry_id": coord.entry_id,
                "users": users,
                "count": len(users),
            },
        )
        _LOGGER.info("DahuaVTO: listed %d users", len(users))

    hass.services.async_register(
        DOMAIN, SERVICE_LEARN_CARD, handle_learn_card, schema=SCHEMA_LEARN_CARD
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_LEARN_CARD, handle_stop_learn_card, schema=SCHEMA_ENTRY_ONLY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_CARD, handle_add_card, schema=SCHEMA_ADD_CARD
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_CARD, handle_delete_card, schema=SCHEMA_DELETE_CARD
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ENROLL_FINGERPRINT, handle_enroll_fingerprint,
        schema=SCHEMA_ENROLL_FINGERPRINT,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ENROLLMENT, handle_cancel_enrollment,
        schema=SCHEMA_ENTRY_ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_USERS, handle_list_users, schema=SCHEMA_ENTRY_ONLY
    )

    # ------------------------------------------------------------------
    # Call / Alarm / Log services
    # ------------------------------------------------------------------

    async def handle_call_room(call: ServiceCall) -> None:
        """Initiate a video call to a VTH indoor unit."""
        coord = _get_coordinator(hass, call)
        room_no = call.data["room_no"]
        ok = await coord.call_room(room_no)
        if ok:
            _LOGGER.info("DahuaVTO: calling room %s", room_no)
        else:
            _LOGGER.warning("DahuaVTO: call_room %s may have failed", room_no)

    async def handle_stop_call(call: ServiceCall) -> None:
        """Hang up an active video call."""
        coord = _get_coordinator(hass, call)
        room_no = call.data.get("room_no", "")
        ok = await coord.stop_call(room_no)
        if ok:
            _LOGGER.info("DahuaVTO: call stopped")
        else:
            _LOGGER.warning("DahuaVTO: stop_call may have failed")

    async def handle_trigger_alarm(call: ServiceCall) -> None:
        """Switch an alarm output on or off, with optional auto-stop."""
        coord = _get_coordinator(hass, call)
        await coord.trigger_alarm(
            channel=call.data.get("channel", 1),
            active=call.data.get("active", True),
            duration=call.data.get("duration", 0),
        )

    async def handle_get_logs(call: ServiceCall) -> None:
        """Fetch recent events and fire 'dahua_vto_logs_fetched' on the HA bus."""
        coord = _get_coordinator(hass, call)
        await coord.get_logs(
            count=call.data.get("count", 20),
            source=call.data.get("source", "both"),
            log_type=call.data.get("log_type", "access"),
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CALL_ROOM, handle_call_room, schema=SCHEMA_CALL_ROOM
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_CALL, handle_stop_call, schema=SCHEMA_STOP_CALL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TRIGGER_ALARM, handle_trigger_alarm, schema=SCHEMA_TRIGGER_ALARM
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_LOGS, handle_get_logs, schema=SCHEMA_GET_LOGS
    )

    _LOGGER.debug("DahuaVTO: services registered")
