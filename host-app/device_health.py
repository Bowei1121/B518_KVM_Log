"""Per-device health record and builder for Core Service status reporting (issue #121)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    try:
        from device_registry import DeviceEntry
    except ImportError:
        from .device_registry import DeviceEntry  # type: ignore[import-not-found,no-redef]


@dataclass
class DeviceHealth:
    """Mutable health record of a single test device's runtime connectivity and lock state."""

    device: str
    station: str
    kvm_ip: str
    connected: bool
    busy: bool
    frozen: bool
    channels: int = 0

    @property
    def status(self) -> str:
        """Operating status string following today's precedence:

        BUSY > FROZEN > IDLE > DISCONNECTED.
        """
        if self.busy:
            return "BUSY"
        if self.frozen:
            return "FROZEN"
        if self.connected:
            return "IDLE"
        return "DISCONNECTED"


def build_health(
    entries: Iterable[DeviceEntry],
    active_devices: Iterable[str],
    frozen_devices: Iterable[str],
    active_ips: Iterable[str],
) -> list[DeviceHealth]:
    """Build a list of DeviceHealth records in registry (entries) order.

    Each entry must have ``.device_id``, ``.station``, ``.kvm_ip`` and ``.channels``.
    """
    active_set = set(active_devices)
    frozen_set = set(frozen_devices)
    ips_set = set(active_ips)

    return [
        DeviceHealth(
            device=entry.device_id,
            station=entry.station,
            kvm_ip=entry.kvm_ip,
            connected=entry.kvm_ip in ips_set,
            busy=entry.device_id in active_set,
            frozen=entry.device_id in frozen_set,
            channels=entry.channels,
        )
        for entry in entries
    ]
