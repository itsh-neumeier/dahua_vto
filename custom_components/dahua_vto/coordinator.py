"""Coordinator for DahuaVTO – manages the persistent event stream."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
        number.  If `user_id` is given the card is also registered on the
        device for that user (creates the user if `user_name` is supplied).
        """
        self._card_learning = True
        self._card_learning_user_id = user_id
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
    # Fingerprint enrollment
    # ------------------------------------------------------------------

    async def start_fingerprint_enrollment(
        self,
        user_id: str,
        user_name: str = "",
        finger_index: int = 0,
        poll_interval: float = 2.0,
        timeout: int = 60,
    ) -> None:
        """Start fingerprint enrollment on the device.

        Workflow:
        1. Optionally create the user if user_name is given.
        2. Tell the device to start enrollment for user_id / finger_index.
        3. Poll enrollment status every `poll_interval` seconds.
        4. Fire 'dahua_vto_fingerprint_enrolled' or 'dahua_vto_fingerprint_failed'
           on the HA bus when done (or timed out).
        """
        # Cancel any ongoing enrollment first
        if self._fp_enroll_task and not self._fp_enroll_task.done():
            self._fp_enroll_task.cancel()

        self._fp_enroll_task = self.hass.async_create_task(
            self._run_fingerprint_enrollment(
                user_id=user_id,
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
