"""Coordinator for DahuaVTO – manages the persistent event stream."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ACCESS_METHOD_CARD,
    FAILURES_BEFORE_UNAVAILABLE,
    RECONNECT_DELAYS,
    SIGNAL_AVAILABILITY,
    SIGNAL_EVENT,
)
from .dahua_client import DahuaAuthError, DahuaClient, DahuaConnectionError
from .models import DahuaEvent, DeviceInfo, ModuleConfig

_LOGGER = logging.getLogger(__name__)

# HA bus events fired by this coordinator
EVENT_CARD_LEARNED = "dahua_vto_card_learned"
EVENT_FP_ENROLLED = "dahua_vto_fingerprint_enrolled"
EVENT_FP_FAILED = "dahua_vto_fingerprint_failed"
EVENT_LOGS_FETCHED = "dahua_vto_logs_fetched"

# How many events to keep in the in-memory log
_EVENT_LOG_MAX = 100

# Fingerprint enrollment status codes returned by the device
_FP_STATUS_IDLE = {"0", "idle"}
_FP_STATUS_ENROLLING = {"1", "enrolling"}
_FP_STATUS_FAILED = {"2", "failed", "error"}
_FP_STATUS_SUCCESS = {"3", "success", "ok", "complete"}


class DahuaCoordinator:
    """
    Manages the Dahua event stream connection.

    - Starts a background task that reads the event stream.
    - On disconnect, waits and reconnects with exponential backoff.
    - Dispatches DahuaEvent objects via HA's dispatcher to all entities.
    - Tracks availability and notifies entities.
    - Supports card learning mode and fingerprint enrollment.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: DahuaClient,
        device_info: DeviceInfo,
        module_config: ModuleConfig,
        entry_id: str,
    ) -> None:
        self.hass = hass
        self.client = client
        self.device_info = device_info
        self.module_config = module_config
        self.entry_id = entry_id

        self._stop_event = asyncio.Event()
        self._stream_task: asyncio.Task | None = None
        self._available = False
        self._consecutive_failures = 0  # reset to 0 on every successful connect

        # Card learning mode state
        self._card_learning: bool = False
        self._card_learning_user_id: str | None = None
        self._card_learning_user_name: str | None = None

        # Fingerprint enrollment task
        self._fp_enroll_task: asyncio.Task | None = None

        # UserID → UserName mapping (loaded at startup, refreshed on reconnect)
        self.user_map: dict[str, str] = {}

        # Rolling in-memory event log (last _EVENT_LOG_MAX events)
        self._event_log: deque[dict] = deque(maxlen=_EVENT_LOG_MAX)

        # Alarm auto-stop task
        self._alarm_stop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the background event stream task."""
        self._stop_event.clear()
        # Pre-load user name map so AccessControl events can show names immediately
        await self._refresh_user_map()
        self._stream_task = self.hass.async_create_background_task(
            self._run_stream_loop(),
            name=f"dahua_vto_{self.entry_id}_stream",
        )

    async def async_stop(self) -> None:
        """Stop the background task cleanly."""
        self._card_learning = False
        if self._fp_enroll_task and not self._fp_enroll_task.done():
            self._fp_enroll_task.cancel()
        if self._alarm_stop_task and not self._alarm_stop_task.done():
            self._alarm_stop_task.cancel()
        self._stop_event.set()
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except (asyncio.CancelledError, Exception):
                pass
        await self.client.close()

    # ------------------------------------------------------------------
    # Internal stream loop
    # ------------------------------------------------------------------

    async def _run_stream_loop(self) -> None:
        """Continuously connect to the event stream, reconnecting on failure."""
        delay_idx = 0

        while not self._stop_event.is_set():
            try:
                _LOGGER.info(
                    "DahuaVTO [%s]: Connecting to event stream", self.entry_id
                )
                await self.client.stream_events(
                    callback=self._on_event,
                    stop_event=self._stop_event,
                    on_connected=self._on_stream_connected,
                )
                # stream_events returned normally (stop_event set)
                delay_idx = 0
                self._consecutive_failures = 0
                break

            except DahuaAuthError:
                _LOGGER.error(
                    "DahuaVTO [%s]: Authentication failed – check credentials",
                    self.entry_id,
                )
                self._set_available(False)
                # No point retrying with wrong credentials
                break

            except DahuaConnectionError as exc:
                self._consecutive_failures += 1
                if self._consecutive_failures >= FAILURES_BEFORE_UNAVAILABLE:
                    _LOGGER.warning(
                        "DahuaVTO [%s]: Stream error (failure #%d): %s – "
                        "marking unavailable, retrying in %ds",
                        self.entry_id,
                        self._consecutive_failures,
                        exc,
                        RECONNECT_DELAYS[delay_idx],
                    )
                    self._set_available(False)
                else:
                    _LOGGER.debug(
                        "DahuaVTO [%s]: Stream dropped (failure #%d): %s – "
                        "reconnecting silently in %ds",
                        self.entry_id,
                        self._consecutive_failures,
                        exc,
                        RECONNECT_DELAYS[0],
                    )

            except Exception as exc:
                self._consecutive_failures += 1
                _LOGGER.exception(
                    "DahuaVTO [%s]: Unexpected stream error (failure #%d): %s",
                    self.entry_id,
                    self._consecutive_failures,
                    exc,
                )
                if self._consecutive_failures >= FAILURES_BEFORE_UNAVAILABLE:
                    self._set_available(False)

            # First failure: reconnect quickly and silently.
            # Subsequent failures: use exponential back-off.
            if self._consecutive_failures <= 1:
                delay = RECONNECT_DELAYS[0]   # 3 s quick retry
            else:
                delay = RECONNECT_DELAYS[delay_idx]
                delay_idx = min(delay_idx + 1, len(RECONNECT_DELAYS) - 1)

            try:
                await asyncio.wait_for(
                    asyncio.shield(
                        asyncio.ensure_future(self._stop_event.wait())
                    ),
                    timeout=delay,
                )
                # stop_event was set during wait → exit
                break
            except asyncio.TimeoutError:
                pass  # Reconnect now

        _LOGGER.info("DahuaVTO [%s]: Stream loop stopped", self.entry_id)

    async def _on_event(self, event: DahuaEvent) -> None:
        """Called for every parsed event from the stream."""
        # Append to in-memory event log
        self._event_log.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "code": event.code,
                "action": event.action,
                "index": event.index,
                "data": event.data,
                "user_id": event.user_id,
                "user_name": self.user_map.get(str(event.user_id), ""),
                "card_no": event.card_no,
            }
        )

        # Check card learning mode BEFORE dispatching to entities
        if self._card_learning and event.code == "AccessControl":
            method = event.access_method
            if method in (ACCESS_METHOD_CARD, 128):  # 128 = face/IC card variants
                card_no = event.card_no or event.data.get("CardNo", "")
                if card_no:
                    await self._on_card_learned(card_no, event.user_id)

        _LOGGER.info(
            "Dispatching event: code=%s action=%s index=%d data=%s",
            event.code,
            event.action,
            event.index,
            event.data,
        )
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_EVENT}_{self.entry_id}",
            event,
        )

    def _on_stream_connected(self) -> None:
        """Called as soon as the HTTP event stream is successfully opened."""
        self._consecutive_failures = 0
        self._set_available(True)
        # Refresh user map in background on every reconnect (catches new users)
        self.hass.async_create_task(self._refresh_user_map())

    def _set_available(self, available: bool) -> None:
        """Update availability and notify entities."""
        if self._available == available:
            return
        self._available = available
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_AVAILABILITY}_{self.entry_id}",
            available,
        )

    @property
    def available(self) -> bool:
        """Return True if the device is connected."""
        return self._available

    async def _refresh_user_map(self) -> None:
        """Load (or refresh) the UserID → UserName mapping from the device.

        Called at startup and on every stream reconnect.  Errors are logged
        but never propagated so they cannot crash the stream loop.
        """
        try:
            users = await self.client.get_users()
            self.user_map = {
                str(u["UserID"]): str(u.get("UserName", u["UserID"]))
                for u in users
                if u.get("UserID")
            }
            _LOGGER.info(
                "DahuaVTO [%s]: User map loaded (%d users): %s",
                self.entry_id, len(self.user_map), self.user_map,
            )
        except Exception as exc:
            _LOGGER.warning(
                "DahuaVTO [%s]: Could not refresh user map: %s", self.entry_id, exc
            )

    # ------------------------------------------------------------------
    # User ID helpers
    # ------------------------------------------------------------------

    def _next_user_id(self) -> str:
        """Return the next available numeric UserID (max existing + 1, min 1001).

        Used when registering a new person without an explicit ID.
        """
        existing = [int(uid) for uid in self.user_map if uid.isdigit()]
        return str(max(existing, default=1000) + 1)

    def _resolve_user_id(self, user_id: str | None, user_name: str | None) -> str | None:
        """Return a UserID to use for card / fingerprint registration.

        Resolution order:
        1. Use ``user_id`` directly if provided.
        2. Look up ``user_name`` in the current user_map (case-insensitive).
        3. Auto-generate the next available numeric UserID.

        Returns None only when neither user_id nor user_name is given
        (= anonymous learn / no registration desired).
        """
        if user_id:
            return user_id
        if not user_name:
            return None
        # Reverse lookup: name → existing ID
        name_lower = user_name.strip().lower()
        found_id = next(
            (uid for uid, nm in self.user_map.items() if nm.lower() == name_lower),
            None,
        )
        if found_id:
            _LOGGER.debug(
                "DahuaVTO [%s]: Resolved user '%s' → existing UserID %s",
                self.entry_id, user_name, found_id,
            )
            return found_id
        # New person – generate next ID
        new_id = self._next_user_id()
        _LOGGER.info(
            "DahuaVTO [%s]: Auto-generated UserID %s for new user '%s'",
            self.entry_id, new_id, user_name,
        )
        return new_id

    # ------------------------------------------------------------------
    # Card learning mode
    # ------------------------------------------------------------------

    async def start_card_learning(
        self,
        user_id: str | None = None,
        user_name: str | None = None,
        timeout: int = 30,
    ) -> None:
        """Enter card learning mode for `timeout` seconds.

        When the next RFID/IC card is presented to the reader, a
        'dahua_vto_card_learned' event is fired on the HA bus with the card
        number.  The card is then registered on the device for the resolved
        user (see _resolve_user_id).  Providing only user_name is enough –
        the integration will find the existing UserID or create a new one.
        """
        resolved_id = self._resolve_user_id(user_id, user_name)
        self._card_learning = True
        self._card_learning_user_id = resolved_id
        self._card_learning_user_name = user_name
        _LOGGER.info(
            "DahuaVTO [%s]: Card learning mode active (%ds, user=%s)",
            self.entry_id, timeout, user_id,
        )

        async def _auto_cancel() -> None:
            await asyncio.sleep(timeout)
            if self._card_learning:
                self._card_learning = False
                _LOGGER.info(
                    "DahuaVTO [%s]: Card learning mode timed out", self.entry_id
                )
                self.hass.bus.async_fire(
                    EVENT_CARD_LEARNED,
                    {
                        "entry_id": self.entry_id,
                        "card_no": None,
                        "user_id": user_id,
                        "status": "timeout",
                    },
                )

        self.hass.async_create_task(_auto_cancel())

    async def _on_card_learned(self, card_no: str, device_user_id: str) -> None:
        """Called when a card scan is detected during learning mode."""
        self._card_learning = False
        _LOGGER.info("DahuaVTO [%s]: Card learned: %s", self.entry_id, card_no)

        # Register card on device if user_id was provided
        if self._card_learning_user_id:
            uid = self._card_learning_user_id
            # Create user record if name was given
            if self._card_learning_user_name:
                await self.client.add_user(uid, self._card_learning_user_name)
            registered = await self.client.add_card(card_no, uid)
            _LOGGER.info(
                "DahuaVTO: Card %s registered for user %s: %s",
                card_no, uid, registered,
            )
        else:
            registered = False

        self.hass.bus.async_fire(
            EVENT_CARD_LEARNED,
            {
                "entry_id": self.entry_id,
                "card_no": card_no,
                "user_id": self._card_learning_user_id or device_user_id,
                "registered": registered,
                "status": "learned",
            },
        )

        # Reset learning state
        self._card_learning_user_id = None
        self._card_learning_user_name = None

    def stop_card_learning(self) -> None:
        """Cancel card learning mode."""
        self._card_learning = False
        self._card_learning_user_id = None
        self._card_learning_user_name = None

    # ------------------------------------------------------------------
    # Alarm control
    # ------------------------------------------------------------------

    async def trigger_alarm(
        self, channel: int = 1, active: bool = True, duration: int = 0
    ) -> None:
        """Switch alarm output on/off with optional auto-stop after `duration` seconds."""
        # Cancel previous auto-stop if any
        if self._alarm_stop_task and not self._alarm_stop_task.done():
            self._alarm_stop_task.cancel()
            self._alarm_stop_task = None

        await self.client.trigger_alarm(channel=channel, active=active)

        if active and duration > 0:
            async def _auto_stop() -> None:
                await asyncio.sleep(duration)
                _LOGGER.info(
                    "DahuaVTO [%s]: Alarm auto-stop after %ds", self.entry_id, duration
                )
                await self.client.trigger_alarm(channel=channel, active=False)

            self._alarm_stop_task = self.hass.async_create_task(_auto_stop())

    # ------------------------------------------------------------------
    # Call control
    # ------------------------------------------------------------------

    async def call_room(self, room_no: str) -> bool:
        """Call a VTH indoor unit by room number."""
        return await self.client.call_room(room_no)

    async def stop_call(self, room_no: str = "") -> bool:
        """Hang up an active call."""
        return await self.client.stop_call(room_no)

    # ------------------------------------------------------------------
    # Log retrieval
    # ------------------------------------------------------------------

    async def get_logs(self, count: int = 20, source: str = "both") -> None:
        """Fetch logs and fire 'dahua_vto_logs_fetched' on the HA bus.

        Parameters
        ----------
        count:  Number of log entries to return.
        source: "device"  – device-side RPC2 log only
                "memory"  – in-memory event log only
                "both"    – merge device + memory logs (default)
        """
        device_records: list[dict] = []
        memory_records: list[dict] = list(self._event_log)[-count:]

        if source in ("device", "both"):
            try:
                device_records = await self.client.get_access_logs(count=count)
            except Exception as exc:
                _LOGGER.debug(
                    "DahuaVTO [%s]: get_access_logs device query failed: %s",
                    self.entry_id, exc,
                )

        if source == "device":
            records = device_records
        elif source == "memory":
            records = memory_records
        else:
            # Merge: device records first (older), memory records last (newer)
            records = device_records + memory_records

        self.hass.bus.async_fire(
            EVENT_LOGS_FETCHED,
            {
                "entry_id": self.entry_id,
                "count": len(records),
                "records": records[-count:],  # cap at requested count
                "source": source,
            },
        )
        _LOGGER.info(
            "DahuaVTO [%s]: get_logs fired %d records (source=%s)",
            self.entry_id, len(records), source,
        )

    # ------------------------------------------------------------------
    # Fingerprint enrollment
    # ------------------------------------------------------------------

    async def start_fingerprint_enrollment(
        self,
        user_id: str | None = None,
        user_name: str = "",
        finger_index: int = 0,
        poll_interval: float = 2.0,
        timeout: int = 60,
    ) -> None:
        """Start fingerprint enrollment on the device.

        Providing only user_name is sufficient – the integration resolves an
        existing UserID or auto-generates a new one (see _resolve_user_id).

        Workflow:
        1. Resolve / generate a UserID.
        2. Create the user record if user_name is given and user is new.
        3. Tell the device to start enrollment for user_id / finger_index.
        4. Poll enrollment status every `poll_interval` seconds.
        5. Fire 'dahua_vto_fingerprint_enrolled' or 'dahua_vto_fingerprint_failed'
           on the HA bus when done (or timed out).
        """
        resolved_id = self._resolve_user_id(user_id, user_name or None)
        if not resolved_id:
            _LOGGER.error(
                "DahuaVTO [%s]: enroll_fingerprint requires user_id or user_name",
                self.entry_id,
            )
            return

        # Cancel any ongoing enrollment first
        if self._fp_enroll_task and not self._fp_enroll_task.done():
            self._fp_enroll_task.cancel()

        self._fp_enroll_task = self.hass.async_create_task(
            self._run_fingerprint_enrollment(
                user_id=resolved_id,
                user_name=user_name,
                finger_index=finger_index,
                poll_interval=poll_interval,
                timeout=timeout,
            )
        )

    async def _run_fingerprint_enrollment(
        self,
        user_id: str,
        user_name: str,
        finger_index: int,
        poll_interval: float,
        timeout: int,
    ) -> None:
        """Background task: drive fingerprint enrollment and poll for completion."""
        _LOGGER.info(
            "DahuaVTO [%s]: Starting fingerprint enrollment user=%s finger=%d",
            self.entry_id, user_id, finger_index,
        )

        # Ensure user exists on device
        if user_name:
            await self.client.add_user(user_id, user_name)

        # Start enrollment mode on device
        started = await self.client.start_fingerprint_enrollment(user_id, finger_index)
        if not started:
            _LOGGER.error(
                "DahuaVTO [%s]: Device did not accept fingerprint enrollment start",
                self.entry_id,
            )
            self.hass.bus.async_fire(
                EVENT_FP_FAILED,
                {
                    "entry_id": self.entry_id,
                    "user_id": user_id,
                    "reason": "device_rejected_start",
                },
            )
            return

        # Poll for status
        elapsed = 0.0
        progress = 0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status_data = await self.client.get_fingerprint_enrollment_status()
            raw_status = str(status_data.get("status", "")).lower()
            raw_progress = status_data.get("progress", status_data.get("enrolltimes", ""))

            try:
                progress = int(raw_progress)
            except (ValueError, TypeError):
                pass

            _LOGGER.debug(
                "FP enrollment status: %s progress=%s", raw_status, progress
            )

            if raw_status in _FP_STATUS_SUCCESS:
                _LOGGER.info(
                    "DahuaVTO [%s]: Fingerprint enrolled for user %s",
                    self.entry_id, user_id,
                )
                self.hass.bus.async_fire(
                    EVENT_FP_ENROLLED,
                    {
                        "entry_id": self.entry_id,
                        "user_id": user_id,
                        "user_name": user_name,
                        "finger_index": finger_index,
                        "status": "success",
                    },
                )
                return

            if raw_status in _FP_STATUS_FAILED:
                _LOGGER.warning(
                    "DahuaVTO [%s]: Fingerprint enrollment failed for user %s",
                    self.entry_id, user_id,
                )
                self.hass.bus.async_fire(
                    EVENT_FP_FAILED,
                    {
                        "entry_id": self.entry_id,
                        "user_id": user_id,
                        "reason": "device_reported_failure",
                    },
                )
                return

        # Timeout reached
        await self.client.stop_fingerprint_enrollment()
        _LOGGER.warning(
            "DahuaVTO [%s]: Fingerprint enrollment timed out for user %s",
            self.entry_id, user_id,
        )
        self.hass.bus.async_fire(
            EVENT_FP_FAILED,
            {
                "entry_id": self.entry_id,
                "user_id": user_id,
                "reason": "timeout",
            },
        )
