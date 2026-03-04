"""Data models for DahuaVTO integration."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DahuaEvent:
    """Represents a single event from the Dahua event stream."""

    code: str
    action: str
    index: int
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_line(cls, line: str) -> "DahuaEvent | None":
        """Parse a raw event line into a DahuaEvent.

        Line format:
          Code=BackKeyLight;action=Start;index=0
          Code=AccessControl;action=Start;index=0;data={"Method":6,"UserID":"101"}
        """
        if not line.startswith("Code="):
            return None

        parts: dict[str, str] = {}
        # Split on ';' but only up to the data= part (data value may contain ';')
        data_split = line.split(";data=", 1)
        meta = data_split[0]
        raw_data = data_split[1] if len(data_split) > 1 else None

        for segment in meta.split(";"):
            if "=" in segment:
                k, v = segment.split("=", 1)
                parts[k.strip()] = v.strip()

        code = parts.get("Code", "")
        action = parts.get("action", "")
        try:
            index = int(parts.get("index", "-1"))
        except ValueError:
            index = -1

        data: dict[str, Any] = {}
        if raw_data:
            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, ValueError):
                pass

        return cls(code=code, action=action, index=index, data=data)

    @property
    def button_number(self) -> int:
        """Return 1-based button number (index -1 → 1 for single-button devices)."""
        return max(1, self.index + 1)

    @property
    def access_method(self) -> int | None:
        """Return the access control method code (if AccessControl event)."""
        return self.data.get("Method")

    @property
    def user_id(self) -> str:
        """Return UserID from AccessControl events."""
        return str(self.data.get("UserID", ""))

    @property
    def card_no(self) -> str:
        """Return CardNo from AccessControl events."""
        return str(self.data.get("CardNo", ""))


@dataclass
class DeviceInfo:
    """Basic device information from magicBox.cgi."""

    model: str = ""
    serial: str = ""
    firmware: str = ""
    hardware: str = ""
    ip: str = ""

    @classmethod
    def from_response(cls, text: str, ip: str = "") -> "DeviceInfo":
        """Parse key=value response from magicBox.cgi getSystemInfo."""
        info = cls(ip=ip)
        for line in text.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().lower().replace("table.", "").replace("devicetype.", "")
            value = value.strip()
            if key in ("type", "devicetype"):
                info.model = value
            elif key == "serialno":
                info.serial = value
            elif key == "softwareversion":
                info.firmware = value
            elif key == "hardwareversion":
                info.hardware = value
        return info


@dataclass
class ModuleConfig:
    """Detected hardware modules on the VTO device."""

    button_count: int = 1
    has_fingerprint: bool = False
    has_card_reader: bool = False
    has_display: bool = False
    has_camera: bool = True  # All VTO have a camera

    @classmethod
    def from_response(cls, text: str) -> "ModuleConfig":
        """Parse VTOExt config response.

        Handles many Dahua/GOLIATH API field name variations:
        - ButtonNumber, ButtonNum, CallButtonNum
        - Button[N].Enable arrays (count enabled entries)
        - FingerprintEnable, Fingerprint, finger_print
        - ICSCardEnable, CardReader, RFID, ICCard
        """
        cfg = cls()
        enabled_buttons: set[int] = set()
        for line in text.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            key_lower = key.lower().replace("table.", "").replace("vtoext.", "")
            value = value.strip()
            value_lower = value.lower()
            bool_true = value_lower in ("true", "1", "yes", "enable", "enabled")

            # --- button count via explicit count field ---
            if any(k in key_lower for k in ("buttonnumber", "buttonnum", "callbuttonnum")):
                try:
                    cfg.button_count = max(cfg.button_count, int(value))
                except ValueError:
                    pass

            # --- button array: Button[N].Enable=true ---
            elif re.search(r"button\[(\d+)\]\.enable", key_lower):
                m = re.search(r"button\[(\d+)\]\.enable", key_lower)
                if m and bool_true:
                    enabled_buttons.add(int(m.group(1)))

            # --- fingerprint ---
            elif any(k in key_lower for k in ("fingerprint", "finger_print", "fingerprintenable")):
                if bool_true:
                    cfg.has_fingerprint = True

            # --- card / RFID reader ---
            elif any(k in key_lower for k in (
                "icscardreader", "icscardenable", "cardreader",
                "rfidenable", "rfid", "iccard", "has_card", "cardreaderenable",
            )):
                if bool_true:
                    cfg.has_card_reader = True

            # --- display ---
            elif "display" in key_lower:
                if bool_true:
                    cfg.has_display = True

        # If enabled button array was found, use it (more reliable than the count field)
        if enabled_buttons:
            cfg.button_count = max(cfg.button_count, len(enabled_buttons))

        return cfg
