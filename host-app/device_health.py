"""Per-device health record and builder for Core Service status reporting (issue #121)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Container, Iterable, Optional

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
    # Connection failure (#134), filled from the KVM Connection Pool's tracker.
    connect_fail_count: int = 0
    connect_failed: bool = False
    retrying: bool = False
    retry_in_sec: Optional[int] = None
    first_fail_at: Optional[float] = None
    last_connect_error: Optional[str] = None

    def apply_connect_state(self, state: Optional[Any], now: float) -> None:
        """Copy the tracker's view of this KVM; ``None`` (no failures recorded) resets to the quiet defaults."""
        if state is None:
            self.connect_fail_count = 0
            self.connect_failed = False
            self.retrying = False
            self.retry_in_sec = None
            self.first_fail_at = None
            self.last_connect_error = None
            return
        self.connect_fail_count = state.fail_count
        self.connect_failed = state.connect_failed
        self.retrying = state.retrying
        self.retry_in_sec = state.retry_in_sec(now)
        self.first_fail_at = state.first_fail_at
        self.last_connect_error = state.last_error

    def refresh(
        self,
        active_devices: Container[str],
        frozen_devices: Container[str],
        active_ips: Container[str],
    ) -> None:
        """Refresh runtime flags using set-like membership."""
        self.busy = self.device in active_devices
        self.frozen = self.device in frozen_devices
        self.connected = self.kvm_ip in active_ips

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

    records = []
    for entry in entries:
        record = DeviceHealth(
            device=entry.device_id,
            station=entry.station,
            kvm_ip=entry.kvm_ip,
            connected=False,
            busy=False,
            frozen=False,
            channels=entry.channels,
        )
        record.refresh(active_set, frozen_set, ips_set)
        records.append(record)
    return records
