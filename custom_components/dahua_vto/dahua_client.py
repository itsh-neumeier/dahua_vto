"""Async HTTP client for Dahua VTO CGI API with event streaming."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from urllib.parse import quote as _url_quote

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import EVENT_CODES, STREAM_TIMEOUT
from .models import DahuaEvent, DeviceInfo, ModuleConfig

_LOGGER = logging.getLogger(__name__)
_CHUNK_SIZE = 4096


# ---------------------------------------------------------------------------
# Input sanitisation helpers
# ---------------------------------------------------------------------------

def _safe_id(value: str, max_len: int = 64) -> str:
    """Return only word characters (digits, letters, underscore) from *value*.

    Used for UserID, CardNo and similar identifier fields that are embedded
    in CGI URL paths.  Prevents path-traversal or parameter-injection via
    crafted IDs.
    """
    return re.sub(r"[^\w\-]", "", str(value), flags=re.ASCII)[:max_len]


def _safe_name(value: str, max_len: int = 64) -> str:
    """URL-encode a free-text field (e.g. UserName) for use in CGI params."""
    return _url_quote(str(value)[:max_len], safe="")


class DahuaAuthError(Exception):
    """Raised when authentication fails (401)."""


class DahuaConnectionError(Exception):
    """Raised when the device is unreachable."""


# ---------------------------------------------------------------------------
# RFC 2617 HTTP Digest Auth (Dahua-compatible)
# ---------------------------------------------------------------------------

def _parse_www_authenticate(header: str) -> dict[str, str]:
    """Parse WWW-Authenticate header into a dict."""
    params: dict[str, str] = {}
    for match in re.finditer(r'(\w+)=["\']?([^"\',$]+)["\']?', header):
        params[match.group(1)] = match.group(2).strip()
    return params


def _build_digest_header(
    method: str,
    uri: str,
    username: str,
    password: str,
    www_auth: str,
) -> str:
    """Compute and return a Digest Authorization header value."""
    params = _parse_www_authenticate(www_auth)
    realm = params.get("realm", "")
    nonce = params.get("nonce", "")
    qop   = params.get("qop", "")
    opaque = params.get("opaque", "")
    algorithm = params.get("algorithm", "MD5").upper()

    def md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    def sha256(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    h = sha256 if "SHA-256" in algorithm else md5

    ha1 = h(f"{username}:{realm}:{password}")
    ha2 = h(f"{method}:{uri}")

    if "auth" in qop:
        nc = "00000001"
        cnonce = os.urandom(8).hex()
        digest = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        parts = (
            f'Digest username="{username}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", algorithm={algorithm}, '
            f'qop=auth, nc={nc}, cnonce="{cnonce}", response="{digest}"'
        )
    else:
        digest = h(f"{ha1}:{nonce}:{ha2}")
        parts = (
            f'Digest username="{username}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", algorithm={algorithm}, '
            f'response="{digest}"'
        )

    if opaque:
        parts += f', opaque="{opaque}"'

    return parts


# ---------------------------------------------------------------------------
# Dahua HTTP Client
# ---------------------------------------------------------------------------

class DahuaClient:
    """HTTP client for Dahua VTO CGI API + RPC2 API."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        session: ClientSession | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._external_session = session  # HA-provided session (not owned by us)
        self._own_session: ClientSession | None = None
        self._base_url = f"http://{host}:{port}"

        # RPC2 session state
        self._rpc2_session: str | None = None
        self._rpc2_id: int = 0

    def _session(self) -> ClientSession:
        """Return an aiohttp session without default auth (we handle auth manually)."""
        if self._external_session and not self._external_session.closed:
            return self._external_session
        if self._own_session is None or self._own_session.closed:
            self._own_session = ClientSession()
        return self._own_session

    async def close(self) -> None:
        """Close the internally created session (not the HA-provided one)."""
        self._rpc2_session = None
        if self._own_session and not self._own_session.closed:
            await self._own_session.close()

    # ------------------------------------------------------------------
    # Core request with Digest auth (two-step: probe → auth)
    # ------------------------------------------------------------------

    async def _get(self, path: str, timeout: int = 10) -> str:
        """GET with automatic Digest auth retry."""
        url = f"{self._base_url}{path}"
        session = self._session()
        to = ClientTimeout(total=timeout)

        # Step 1: send without auth to obtain the challenge
        try:
            async with session.get(url, timeout=to, ssl=False) as resp:
                if resp.status == 200:
                    return await resp.text()
                if resp.status != 401:
                    resp.raise_for_status()

                www_auth = resp.headers.get("WWW-Authenticate", "")

        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Cannot reach {url}: {exc}") from exc

        # Step 2: send with computed Digest header
        if not www_auth:
            raise DahuaAuthError("Device returned 401 without WWW-Authenticate")

        try:
            auth_header = _build_digest_header(
                method="GET",
                uri=path,
                username=self._username,
                password=self._password,
                www_auth=www_auth,
            )
        except Exception as exc:
            raise DahuaAuthError(f"Could not build Digest header: {exc}") from exc

        try:
            async with session.get(
                url,
                headers={"Authorization": auth_header},
                timeout=to,
                ssl=False,
            ) as resp2:
                if resp2.status == 401:
                    raise DahuaAuthError(
                        "Invalid credentials – check username and password"
                    )
                resp2.raise_for_status()
                return await resp2.text()

        except DahuaAuthError:
            raise
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Request failed for {url}: {exc}") from exc

    async def _get_stream(
        self,
        path: str,
        sock_read_timeout: float | None = STREAM_TIMEOUT,
    ) -> tuple[aiohttp.ClientResponse, str]:
        """
        Open a persistent GET for streaming.
        Returns (response, auth_header_to_reuse).
        Caller is responsible for closing the response.
        """
        url = f"{self._base_url}{path}"
        session = self._session()
        to = ClientTimeout(total=None, sock_connect=10, sock_read=sock_read_timeout)

        # Probe for Digest challenge
        try:
            probe = await session.get(url, timeout=ClientTimeout(total=10), ssl=False)
            www_auth = probe.headers.get("WWW-Authenticate", "")
            await probe.release()
            if probe.status not in (401, 200):
                probe.raise_for_status()
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Cannot reach {url}: {exc}") from exc

        auth_header = ""
        if www_auth:
            auth_header = _build_digest_header(
                method="GET",
                uri=path,
                username=self._username,
                password=self._password,
                www_auth=www_auth,
            )

        headers = {}
        if auth_header:
            headers["Authorization"] = auth_header

        resp = await session.get(url, headers=headers, timeout=to, ssl=False)
        if resp.status == 401:
            await resp.release()
            raise DahuaAuthError("Invalid credentials for event stream")
        if resp.status != 200:
            await resp.release()
            resp.raise_for_status()

        return resp, auth_header

    # ------------------------------------------------------------------
    # Dahua RPC2 API (JSON-RPC over HTTP POST to /RPC2)
    # Used for user management (AccessUser.*) on newer/GOLIATH firmware.
    # ------------------------------------------------------------------

    async def _rpc2_login(self) -> str:
        """Perform Dahua two-step RPC2 login and return a session token.

        Formula confirmed from device web-UI JavaScript (S.a = MD5+toUpperCase):
          Step 1: Send empty password → receive realm + random challenge
          Step 2: Compute hashes:
            pwd_hash   = MD5(username + ":" + realm + ":" + password).upper()
            login_pass = MD5(username + ":" + random + ":" + pwd_hash).upper()
          Echo back realm, random, passwordType, authorityType in the second request.
        """
        url = f"{self._base_url}/RPC2_Login"
        session = self._session()
        to = ClientTimeout(total=10)

        # ---- Step 1: challenge ----------------------------------------
        self._rpc2_id += 1
        payload1 = {
            "method": "global.login",
            "params": {
                "userName": self._username,
                "password": "",
                "clientType": "Web3.0",
                "ipAddr": "0.0.0.0",
                "loginType": "Direct",
            },
            "id": self._rpc2_id,
            "session": 0,
        }
        try:
            async with session.post(
                url, json=payload1, timeout=to, ssl=False
            ) as resp:
                challenge = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"RPC2 login step1 failed: {exc}") from exc

        # Some devices accept empty-password login and return result=true directly
        if challenge.get("result") and challenge.get("session"):
            return str(challenge["session"])

        params    = challenge.get("params") or {}
        random_v  = params.get("random", "")
        realm     = params.get("realm", "")
        encrypt   = params.get("encryption", "Default")
        partial   = challenge.get("session", "")

        # ---- Step 2: authenticate with hashed password -----------------
        # Both hashes use .upper() – confirmed from device JS (MD5+toUpperCase).
        pwd_hash   = hashlib.md5(
            f"{self._username}:{realm}:{self._password}".encode()
        ).hexdigest().upper()
        login_pass = hashlib.md5(
            f"{self._username}:{random_v}:{pwd_hash}".encode()
        ).hexdigest().upper()

        self._rpc2_id += 1
        payload2 = {
            "method": "global.login",
            "params": {
                "userName": self._username,
                "password": login_pass,
                "clientType": "Web3.0",
                "realm": realm,
                "random": random_v,
                "passwordType": "Default",
                "authorityType": encrypt,
            },
            "id": self._rpc2_id,
            "session": partial,
        }
        try:
            async with session.post(
                url, json=payload2, timeout=to, ssl=False
            ) as resp2:
                auth = await resp2.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"RPC2 login step2 failed: {exc}") from exc

        if not (auth and auth.get("result")):
            raise DahuaAuthError(f"RPC2 login rejected: {auth}")

        token = auth.get("session") or partial
        _LOGGER.debug("RPC2 login successful, session=%s…", str(token)[:8])
        return str(token)

    async def _rpc2_call(self, method: str, params: dict) -> dict:
        """Make an authenticated RPC2 call.

        Auto-logins on first use.  On session-expired error (-401 / 287637505)
        re-logins once and retries.
        """
        if self._rpc2_session is None:
            self._rpc2_session = await self._rpc2_login()

        url = f"{self._base_url}/RPC2"
        session = self._session()
        to = ClientTimeout(total=15)

        async def _do_call(sess_token: str) -> dict:
            self._rpc2_id += 1
            body = {
                "method": method,
                "params": params,
                "id": self._rpc2_id,
                "session": sess_token,
            }
            try:
                async with session.post(
                    url, json=body, timeout=to, ssl=False
                ) as resp:
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as exc:
                raise DahuaConnectionError(f"RPC2 call {method} failed: {exc}") from exc

        result = await _do_call(self._rpc2_session)

        # Handle expired / invalid session
        err_code = (result.get("error") or {}).get("code", 0)
        if not result.get("result") and err_code in (-401, 287637505, 268894211):
            _LOGGER.debug("RPC2 session expired – re-logging in")
            self._rpc2_session = await self._rpc2_login()
            result = await _do_call(self._rpc2_session)

        return result

    # ------------------------------------------------------------------
    # Discovery / Info API
    # ------------------------------------------------------------------

    async def get_system_info(self) -> DeviceInfo:
        """Query device type and serial number."""
        text = ""
        for action in ("getSystemInfo", "getDeviceType"):
            try:
                text += "\n" + await self._get(
                    f"/cgi-bin/magicBox.cgi?action={action}"
                )
            except Exception:
                pass
        return DeviceInfo.from_response(text, ip=self._host)

    async def get_module_config(self) -> ModuleConfig:
        """Query multiple endpoints to detect installed modules."""
        combined_text = ""

        # Query all known config section names – combine the responses so the
        # parser sees every field regardless of which section owns it.
        for name in (
            "VTOExt",
            "Vto",
            "VTO",
            "Hardware",
            "AccessControl",
            "Encode",
            "T2UServer",
        ):
            try:
                text = await self._get(
                    f"/cgi-bin/configManager.cgi?action=getConfig&name={name}"
                )
                combined_text += "\n" + text
                _LOGGER.debug("getConfig %s → %s", name, text[:200])
            except Exception as exc:
                _LOGGER.debug("getConfig %s failed: %s", name, exc)

        cfg = ModuleConfig.from_response(combined_text)
        _LOGGER.debug(
            "Detected modules: buttons=%d fp=%s card=%s",
            cfg.button_count,
            cfg.has_fingerprint,
            cfg.has_card_reader,
        )
        return cfg

    async def get_snapshot(self) -> bytes | None:
        """Fetch a JPEG snapshot from the device."""
        path = "/cgi-bin/snapshot.cgi?channel=1"
        resp, _ = await self._get_stream(path, sock_read_timeout=5)
        try:
            return await resp.read()
        finally:
            resp.release()

    async def test_connection(self) -> bool:
        """Return True if we can authenticate and reach the device."""
        await self.get_system_info()
        return True

    # ------------------------------------------------------------------
    # Control API
    # ------------------------------------------------------------------

    async def open_door(self, channel: int = 1) -> bool:
        """Trigger the door relay."""
        path = (
            f"/cgi-bin/accessControl.cgi"
            f"?action=openDoor&channel={channel}&UserID=101&Type=Remote"
        )
        try:
            text = await self._get(path)
            _LOGGER.debug("openDoor response: %s", text)
            return "OK" in text or "true" in text.lower()
        except Exception as exc:
            _LOGGER.error("Failed to open door: %s", exc)
            return False

    # ------------------------------------------------------------------
    # User / Card / Fingerprint management
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_records(text: str) -> list[dict]:
        """Parse RecordFinder response into a list of dicts.

        Response format:
          found=2
          record[0].UserID=101
          record[0].UserName=Max
          record[1].UserID=102
          ...
        """
        records: dict[int, dict] = {}
        for line in text.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # match record[N].Field
            import re as _re
            m = _re.match(r"record\[(\d+)\]\.(.+)", key, _re.IGNORECASE)
            if m:
                idx = int(m.group(1))
                field = m.group(2).strip()
                records.setdefault(idx, {})[field] = value.strip()
        return list(records.values())

    async def get_users(self) -> list[dict]:
        """Return all registered users via RPC2 AccessUser API.

        Uses the same method as the device web-UI (Personal Management page):
          AccessUser.startFind → AccessUser.doFind → AccessUser.stopFind

        Falls back to legacy CGI RecordFinder if the RPC2 API is unavailable.
        Each returned dict contains at least: UserID, UserName, UserType,
        Doors, ValidFrom, ValidTo (mirrors the HAR-captured data structure).
        """
        try:
            return await self._get_users_rpc2()
        except Exception as exc:
            _LOGGER.warning(
                "get_users RPC2 failed (%s) – falling back to CGI", exc
            )
            return await self._get_users_cgi()

    async def _get_users_rpc2(self) -> list[dict]:
        """Fetch users via AccessUser RPC2 API (paginated)."""
        # Start find – no filter condition = all users
        start = await self._rpc2_call("AccessUser.startFind", {"Condition": {}})
        if not start.get("result"):
            raise RuntimeError(f"AccessUser.startFind failed: {start}")

        token = start["params"]["Token"]
        total = start["params"].get("Total", 0)
        _LOGGER.debug("AccessUser.startFind: token=%s total=%d", token, total)

        users: list[dict] = []
        offset = 0
        page_size = 20

        try:
            while offset < total:
                find = await self._rpc2_call(
                    "AccessUser.doFind",
                    {"Token": token, "Offset": offset, "Count": page_size},
                )
                if not find.get("result"):
                    _LOGGER.warning("AccessUser.doFind failed at offset %d: %s", offset, find)
                    break
                batch: list[dict] = find.get("params", {}).get("Info", [])
                if not batch:
                    break
                users.extend(batch)
                offset += len(batch)
        finally:
            try:
                await self._rpc2_call("AccessUser.stopFind", {"Token": token})
            except Exception:
                pass

        _LOGGER.info("AccessUser RPC2: fetched %d user(s)", len(users))
        return users

    async def _get_users_cgi(self) -> list[dict]:
        """Legacy CGI fallback: PersonInfo via RecordFinder.cgi."""
        try:
            text = await self._get(
                "/cgi-bin/RecordFinder.cgi?action=find&name=PersonInfo"
                "&count=200&StartFind=1"
            )
            return self._parse_records(text)
        except Exception as exc:
            _LOGGER.debug("_get_users_cgi failed: %s", exc)
            return []

    async def add_user(self, user_id: str, name: str) -> bool:
        """Add or update a user in the access control database."""
        safe_uid  = _safe_id(user_id)
        safe_name = _safe_name(name)
        path = (
            "/cgi-bin/RecordUpdater.cgi?action=insertRecord&name=PersonInfo"
            f"&UserID={safe_uid}&UserName={safe_name}&Enable=true&Authority=General"
        )
        try:
            text = await self._get(path)
            ok = "OK" in text or "true" in text.lower()
            _LOGGER.debug("add_user %s (%s) → %s", safe_uid, name, text.strip())
            return ok
        except Exception as exc:
            _LOGGER.error("add_user failed: %s", exc)
            return False

    async def delete_user(self, user_id: str) -> bool:
        """Remove a user from the access control database."""
        safe_uid = _safe_id(user_id)
        path = (
            "/cgi-bin/RecordUpdater.cgi?action=removeRecord&name=PersonInfo"
            f"&Condition.UserID={safe_uid}"
        )
        try:
            text = await self._get(path)
            return "OK" in text
        except Exception as exc:
            _LOGGER.error("delete_user failed: %s", exc)
            return False

    async def get_cards(self) -> list[dict]:
        """Return all registered RFID cards."""
        try:
            text = await self._get(
                "/cgi-bin/RecordFinder.cgi?action=find&name=AccessControlCard"
                "&count=500&StartFind=1"
            )
            return self._parse_records(text)
        except Exception as exc:
            _LOGGER.debug("get_cards failed: %s", exc)
            return []

    async def add_card(self, card_no: str, user_id: str) -> bool:
        """Register an RFID/IC card for a user (CardType 0 = normal card)."""
        safe_card = _safe_id(card_no)
        safe_uid  = _safe_id(user_id)
        path = (
            "/cgi-bin/RecordUpdater.cgi?action=insertRecord&name=AccessControlCard"
            f"&CardNo={safe_card}&UserID={safe_uid}&CardType=0&Enable=true"
        )
        try:
            text = await self._get(path)
            ok = "OK" in text or "true" in text.lower()
            _LOGGER.debug("add_card %s → user %s: %s", safe_card, safe_uid, text.strip())
            return ok
        except Exception as exc:
            _LOGGER.error("add_card failed: %s", exc)
            return False

    async def delete_card(self, card_no: str) -> bool:
        """Remove an RFID card from the device."""
        safe_card = _safe_id(card_no)
        path = (
            "/cgi-bin/RecordUpdater.cgi?action=removeRecord&name=AccessControlCard"
            f"&Condition.CardNo={safe_card}"
        )
        try:
            text = await self._get(path)
            return "OK" in text
        except Exception as exc:
            _LOGGER.error("delete_card failed: %s", exc)
            return False

    async def start_fingerprint_enrollment(
        self, user_id: str, finger_index: int = 0
    ) -> bool:
        """Start fingerprint enrollment on the device.

        After calling this, the user must place their finger on the scanner
        ~3 times as prompted on the device display.
        Poll get_fingerprint_enrollment_status() to track progress.
        Dahua devices respond to two known endpoint patterns.
        """
        safe_uid = _safe_id(user_id)
        safe_fi  = int(finger_index)  # already typed as int, cast for safety
        for path in (
            # Pattern A: newer firmware
            f"/cgi-bin/AccessControl.cgi?action=startEnroll"
            f"&type=FingerPrint&userID={safe_uid}&fingerIndex={safe_fi}",
            # Pattern B: older firmware
            f"/cgi-bin/AccessControl.cgi?action=startFingerEnroll"
            f"&UserID={safe_uid}&FingerIndex={safe_fi}",
        ):
            try:
                text = await self._get(path)
                if "OK" in text or "true" in text.lower():
                    _LOGGER.info(
                        "Fingerprint enrollment started for user %s (finger %d)",
                        user_id, finger_index,
                    )
                    return True
            except Exception as exc:
                _LOGGER.debug("start_fingerprint_enrollment path %s: %s", path, exc)
        _LOGGER.error(
            "start_fingerprint_enrollment: no working endpoint found for this device"
        )
        return False

    async def get_fingerprint_enrollment_status(self) -> dict:
        """Poll the device for fingerprint enrollment progress.

        Returns dict with at least:
          status  – int: 0=idle, 1=enrolling, 2=failed, 3=success
          progress – int: how many captures done (usually 0–3)
        """
        for path in (
            "/cgi-bin/AccessControl.cgi?action=getEnrollStatus&type=FingerPrint",
            "/cgi-bin/AccessControl.cgi?action=getFingerEnrollStatus",
        ):
            try:
                text = await self._get(path)
                result: dict = {}
                for line in text.splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        result[k.strip().lower()] = v.strip()
                if result:
                    return result
            except Exception:
                continue
        return {"status": "unknown"}

    async def stop_fingerprint_enrollment(self) -> bool:
        """Cancel any ongoing fingerprint enrollment session."""
        try:
            text = await self._get(
                "/cgi-bin/AccessControl.cgi?action=stopEnroll&type=FingerPrint"
            )
            return "OK" in text
        except Exception as exc:
            _LOGGER.debug("stop_fingerprint_enrollment: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Call / Alarm API
    # ------------------------------------------------------------------

    async def call_room(self, room_no: str) -> bool:
        """Initiate a video call to a VTH indoor unit by room number.

        Tries two known CGI endpoint patterns used across different
        Dahua / GOLIATH firmware versions.
        """
        safe_room = _safe_id(room_no)
        for path in (
            f"/cgi-bin/IntervideoManager.cgi"
            f"?action=Start&SessionName=Dahua_IPC&Command=Call&Room={safe_room}",
            f"/cgi-bin/CallManager.cgi?action=startCall&RoomNo={safe_room}",
        ):
            try:
                text = await self._get(path)
                if "OK" in text or "true" in text.lower():
                    _LOGGER.info("call_room %s: OK", safe_room)
                    return True
            except Exception as exc:
                _LOGGER.debug("call_room path %s: %s", path, exc)
        _LOGGER.error("call_room: no working endpoint found for room %s", room_no)
        return False

    async def stop_call(self, room_no: str = "") -> bool:
        """Hang up an active video call."""
        safe_room = _safe_id(room_no) if room_no else ""
        paths = []
        if safe_room:
            paths.append(
                f"/cgi-bin/IntervideoManager.cgi"
                f"?action=Stop&SessionName=Dahua_IPC&Command=Call&Room={safe_room}"
            )
        paths.append(
            "/cgi-bin/IntervideoManager.cgi"
            "?action=Stop&SessionName=Dahua_IPC&Command=Call"
        )
        for path in paths:
            try:
                text = await self._get(path)
                if "OK" in text or "true" in text.lower():
                    _LOGGER.info("stop_call: OK")
                    return True
            except Exception as exc:
                _LOGGER.debug("stop_call path %s: %s", path, exc)
        return False

    async def trigger_alarm(self, channel: int = 1, active: bool = True) -> bool:
        """Switch an alarm output relay on (active=True) or off (active=False).

        Supported by most Dahua VTO devices via the alarmManager CGI.
        """
        action_val = "1" if active else "0"
        path = (
            f"/cgi-bin/alarmManager.cgi"
            f"?action=setAlarmOut&Channel={channel}&Action={action_val}"
        )
        try:
            text = await self._get(path)
            ok = "OK" in text or "true" in text.lower()
            _LOGGER.info(
                "trigger_alarm ch=%d active=%s → %s", channel, active, text.strip()
            )
            return ok
        except Exception as exc:
            _LOGGER.error("trigger_alarm failed: %s", exc)
            return False

    async def get_access_logs(self, count: int = 20) -> list[dict]:
        """Fetch recent access log entries from the device via RPC2 (best-effort).

        Tries RecordFinder (AccessControlLog) first, then LogServer.getMlogList.
        Returns an empty list if both fail (coordinator in-memory log is the
        reliable fallback).
        """
        # --- Attempt 1: RecordFinder for AccessControlLog ------------------
        try:
            start = await self._rpc2_call(
                "RecordFinder.startFind",
                {"Name": "AccessControlLog", "Condition": {}},
            )
            if start.get("result"):
                token = start["params"]["Token"]
                total = start["params"].get("Total", 0)
                records: list[dict] = []
                try:
                    offset = max(0, total - count)
                    do = await self._rpc2_call(
                        "RecordFinder.doFind",
                        {"Token": token, "Offset": offset, "Count": count},
                    )
                    if do.get("result"):
                        records = do.get("params", {}).get("Records", [])
                finally:
                    try:
                        await self._rpc2_call(
                            "RecordFinder.stopFind", {"Token": token}
                        )
                    except Exception:
                        pass
                if records:
                    _LOGGER.debug(
                        "get_access_logs: %d records via RecordFinder", len(records)
                    )
                    return records
        except Exception as exc:
            _LOGGER.debug("get_access_logs RecordFinder failed: %s", exc)

        # --- Attempt 2: LogServer.getMlogList -------------------------------
        try:
            result = await self._rpc2_call(
                "LogServer.getMlogList",
                {"Condition": {}, "Offset": 0, "Count": count},
            )
            if result.get("result"):
                records = result.get("params", {}).get("Records", [])
                _LOGGER.debug(
                    "get_access_logs: %d records via LogServer", len(records)
                )
                return records
        except Exception as exc:
            _LOGGER.debug("get_access_logs LogServer failed: %s", exc)

        return []

    # ------------------------------------------------------------------
    # Event Stream
    # ------------------------------------------------------------------

    async def stream_events(
        self,
        callback: Callable[[DahuaEvent], Awaitable[None]],
        stop_event: asyncio.Event,
        on_connected: Callable[[], None] | None = None,
    ) -> None:
        """
        Connect to the Dahua event stream and call `callback` for each event.
        Runs until `stop_event` is set or connection drops.
        Does NOT reconnect – the coordinator handles that.
        `on_connected` is called once the HTTP stream is successfully opened.
        """
        codes = "[" + ",".join(EVENT_CODES) + "]"
        path = f"/cgi-bin/eventManager.cgi?action=attach&codes={codes}"

        _LOGGER.debug("Connecting to event stream: %s%s", self._base_url, path)

        resp, _ = await self._get_stream(path)

        # Stream is open – notify caller that the device is reachable
        if on_connected is not None:
            on_connected()

        try:
            buffer = b""

            # State for multi-line JSON event data.
            # Dahua VTO devices send event data as multi-line JSON, e.g.:
            #   Code=AccessControl;action=Pulse;index=0;data={
            #   "Method" : 6,
            #   "UserID" : "1011"
            #   }
            # We buffer lines until all opened braces are closed.
            _pending_prefix: str | None = None  # everything up to and incl. "data="
            _pending_data: str = ""             # accumulated JSON text
            _pending_depth: int = 0             # unclosed brace counter

            async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
                if stop_event.is_set():
                    return

                buffer += chunk

                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace").strip()

                    if not line:
                        continue

                    # Log lines at appropriate levels for diagnostics.
                    if line.startswith("Code="):
                        _LOGGER.info("Stream event line: %s", line)
                    elif _pending_prefix is not None:
                        _LOGGER.debug("Stream JSON continuation: %r", line)
                    else:
                        _LOGGER.debug("Stream raw line: %r", line)

                    # Multipart boundary → reset any stale pending state.
                    if line.startswith("--"):
                        if _pending_prefix is not None:
                            _LOGGER.warning(
                                "Multipart boundary while collecting JSON – discarding incomplete event"
                            )
                            _pending_prefix = None
                            _pending_data = ""
                            _pending_depth = 0
                        continue

                    # MIME headers (Content-Type, Content-Length, …) – skip.
                    if re.match(r"^[A-Za-z\-]+:\s", line):
                        continue

                    # SSE format: "data: Code=…"
                    if line.lower().startswith("data:"):
                        line = line[5:].strip()

                    # ---- Collecting multi-line JSON continuation ----------
                    if _pending_prefix is not None:
                        _pending_data += " " + line
                        _pending_depth += line.count("{") - line.count("}")
                        if _pending_depth <= 0:
                            # JSON is now complete – assemble and parse.
                            full_line = _pending_prefix + _pending_data.strip()
                            _pending_prefix = None
                            _pending_data = ""
                            _pending_depth = 0
                            _LOGGER.info("Assembled multi-line event: %s", full_line)
                            event = DahuaEvent.from_line(full_line)
                            if event and event.code:
                                _LOGGER.info(
                                    "Parsed event: code=%s action=%s index=%d data=%s",
                                    event.code, event.action, event.index, event.data,
                                )
                                await callback(event)
                        continue

                    # ---- Only process Code= lines ------------------------
                    if not line.startswith("Code="):
                        continue

                    if ";data=" in line:
                        sep_idx = line.index(";data=") + len(";data=")
                        data_part = line[sep_idx:]
                        depth = data_part.count("{") - data_part.count("}")
                        if depth <= 0:
                            # Single-line event (complete JSON or no JSON body).
                            event = DahuaEvent.from_line(line)
                            if event and event.code:
                                _LOGGER.info(
                                    "Parsed event: code=%s action=%s index=%d data=%s",
                                    event.code, event.action, event.index, event.data,
                                )
                                await callback(event)
                        else:
                            # Multi-line JSON – enter buffering mode.
                            _pending_prefix = line[:sep_idx]
                            _pending_data = data_part
                            _pending_depth = depth
                            _LOGGER.debug(
                                "Starting multi-line JSON: prefix=%r depth=%d",
                                _pending_prefix, _pending_depth,
                            )
                    else:
                        # No data field – parse directly.
                        event = DahuaEvent.from_line(line)
                        if event and event.code:
                            _LOGGER.info(
                                "Parsed event: code=%s action=%s index=%d data=%s",
                                event.code, event.action, event.index, event.data,
                            )
                            await callback(event)

        except aiohttp.ServerTimeoutError as exc:
            raise DahuaConnectionError("Event stream timed out") from exc
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Event stream error: {exc}") from exc
        finally:
            resp.release()
