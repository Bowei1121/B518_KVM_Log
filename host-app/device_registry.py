"""Device registry: load and validate the host-PC device list (devices.json).

The device list is the single source of truth for device ID -> station,
KVM IP and test channel count (ADR-0007). Core Service refuses to start when
the file is missing or invalid; Monitor UI settings page shares the same
validation rules.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

DEVICES_FILE_ENV = "B518_DEVICES_FILE"

STATIONS = ("DFU", "FCT", "BT")
_DEVICE_ID_RE = re.compile(r"^([A-Z]+)-(\d{2})$")


@dataclass(frozen=True)
class DeviceEntry:
    """One configured device."""

    device_id: str
    station: str
    kvm_ip: str
    channels: int


def default_registry_path() -> Path:
    """Device list location: ``$B518_DEVICES_FILE`` if set, else ``~/Documents/template/devices.json``."""
    override = os.environ.get(DEVICES_FILE_ENV)
    if override:
        return Path(override)
    return Path.home() / "Documents" / "template" / "devices.json"


class DeviceRegistryError(Exception):
    """Raised when the device list is missing or invalid; ``errors`` lists every problem."""

    def __init__(self, errors: List[str], reason: str = "devices_invalid") -> None:
        self.reason = reason
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class DeviceRegistry:
    """Immutable, ordered view of the device list. Lookup is exact and case-sensitive."""

    def __init__(self, entries: List[DeviceEntry]) -> None:
        self._entries: Tuple[DeviceEntry, ...] = tuple(entries)
        self._by_id: Dict[str, DeviceEntry] = {e.device_id: e for e in entries}

    @classmethod
    def load(cls, path: Union[str, Path, None] = None) -> "DeviceRegistry":
        """Load and validate the device list at ``path`` (defaults to the template root)."""
        file_path = Path(path) if path is not None else default_registry_path()
        if not file_path.is_file():
            raise DeviceRegistryError([f"device list not found: {file_path}"], reason="devices_missing")
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DeviceRegistryError([f"cannot read device list {file_path}: {exc}"]) from exc
        return cls.from_text(text)

    @classmethod
    def from_text(cls, text: str) -> "DeviceRegistry":
        """Parse and validate device list JSON text."""
        duplicate_keys: List[str] = []

        def _collect_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            obj: Dict[str, Any] = {}
            for key, value in pairs:
                if key in obj:
                    duplicate_keys.append(key)
                obj[key] = value
            return obj

        try:
            data = json.loads(text, object_pairs_hook=_collect_pairs)
        except json.JSONDecodeError as exc:
            raise DeviceRegistryError([f"invalid JSON: {exc}"]) from exc

        errors = [f"duplicate device ID '{key}'" for key in duplicate_keys]
        entries, validation_errors = validate_device_data(data)
        errors.extend(validation_errors)
        if errors:
            raise DeviceRegistryError(errors)
        return cls(entries)

    def get(self, device_id: str) -> Optional[DeviceEntry]:
        """Return the entry for ``device_id`` (exact match) or None."""
        return self._by_id.get(device_id)

    def device_for_ip(self, ip: str) -> Optional[DeviceEntry]:
        """Return the entry whose KVM IP is ``ip`` (kvm_ip values are unique) or None."""
        return next((e for e in self._entries if e.kvm_ip == ip), None)

    def entries(self) -> List[DeviceEntry]:
        """Return all entries in file order."""
        return list(self._entries)


def validate_device_data(data: Any) -> Tuple[List[DeviceEntry], List[str]]:
    """Validate parsed device list data. Returns (entries, errors); errors name the device ID."""
    if not isinstance(data, dict):
        return [], ["device list must be a JSON object keyed by device ID"]

    entries: List[DeviceEntry] = []
    errors: List[str] = []
    seen_ips: Dict[str, str] = {}

    if not data:
        return [], ["device list is empty"]

    for device_id, raw in data.items():
        label = str(device_id)
        match = _DEVICE_ID_RE.match(label)
        if match is None:
            errors.append(f"{label}: device ID must match <站別>-<兩位數>")
            continue
        id_prefix = match.group(1)

        if not isinstance(raw, dict):
            errors.append(f"{label}: entry must be an object")
            continue

        station = raw.get("station")
        kvm_ip = raw.get("kvm_ip")
        channels = raw.get("channels")
        row_errors: List[str] = []

        if station not in STATIONS:
            row_errors.append(f"station must be one of {', '.join(STATIONS)}")
        elif id_prefix != station:
            row_errors.append(f"ID prefix '{id_prefix}' does not match station '{station}'")

        ip_ok = False
        if not isinstance(kvm_ip, str):
            row_errors.append("kvm_ip must be an IPv4 string")
        else:
            try:
                ipaddress.IPv4Address(kvm_ip)
                ip_ok = True
            except ValueError:
                row_errors.append(f"kvm_ip '{kvm_ip}' is not a valid IPv4 address")

        if isinstance(channels, bool) or not isinstance(channels, int) or channels < 1:
            row_errors.append("channels must be a positive integer")

        if ip_ok:
            if kvm_ip in seen_ips:
                row_errors.append(f"kvm_ip '{kvm_ip}' is also used by {seen_ips[kvm_ip]}")
            else:
                seen_ips[kvm_ip] = label

        if row_errors:
            errors.extend(f"{label}: {reason}" for reason in row_errors)
            continue

        entries.append(DeviceEntry(device_id=label, station=station, kvm_ip=kvm_ip, channels=channels))

    return entries, errors
