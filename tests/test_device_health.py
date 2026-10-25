import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_health import DeviceHealth, build_health
from device_registry import DeviceEntry, DeviceRegistry
from freeze_guard import FrameFreezeGuard
from kvm_pool import PooledKVMConnection
from protocol_helpers import make_registry
from tcp_server import CommandContext, TcpJsonServer


# ---------------------------------------------------------------------------
# 1) Pure tests for DeviceHealth.status precedence & build_health
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "busy,frozen,connected,expected",
    [
        (True, True, True, "BUSY"),
        (True, True, False, "BUSY"),
        (True, False, True, "BUSY"),
        (True, False, False, "BUSY"),
        (False, True, True, "FROZEN"),
        (False, True, False, "FROZEN"),
        (False, False, True, "IDLE"),
        (False, False, False, "DISCONNECTED"),
    ],
)
def test_device_health_status_precedence_all_eight_combinations(
    busy: bool, frozen: bool, connected: bool, expected: str
):
    """DeviceHealth.status precedence: BUSY > FROZEN > IDLE > DISCONNECTED."""
    h = DeviceHealth(
        device="DFU-01",
        station="DFU",
        kvm_ip="192.168.1.101",
        connected=connected,
        busy=busy,
        frozen=frozen,
    )
    assert h.status == expected


def test_build_health_ordering_and_membership():
    """build_health returns DeviceHealth records in registry order with correct membership flags."""
    entries = [
        DeviceEntry("DFU-01", "DFU", "192.168.200.1", 4),
        DeviceEntry("DFU-02", "DFU", "192.168.200.2", 4),
        DeviceEntry("FCT-01", "FCT", "192.168.200.3", 2),
        DeviceEntry("BT-01", "BT", "192.168.200.4", 1),
    ]
    # Active devices: DFU-01 and BT-01, plus an extraneous device
    active_devices = ["DFU-01", "BT-01", "UNKNOWN-99"]
    # Frozen devices: DFU-02 and BT-01, plus an extraneous device
    frozen_devices = ["DFU-02", "BT-01", "UNKNOWN-88"]
    # Active IPs: DFU-01 and FCT-01, plus an extraneous IP
    active_ips = ["192.168.200.1", "192.168.200.3", "10.0.0.1"]

    records = build_health(entries, active_devices, frozen_devices, active_ips)

    # 1. Ordering and length match entries exactly
    assert len(records) == 4
    assert [r.device for r in records] == ["DFU-01", "DFU-02", "FCT-01", "BT-01"]
    assert [r.station for r in records] == ["DFU", "DFU", "FCT", "BT"]
    assert [r.kvm_ip for r in records] == [
        "192.168.200.1",
        "192.168.200.2",
        "192.168.200.3",
        "192.168.200.4",
    ]

    # 2. Membership flags
    # DFU-01: busy=True, frozen=False, connected=True -> BUSY
    assert records[0].busy is True
    assert records[0].frozen is False
    assert records[0].connected is True
    assert records[0].status == "BUSY"

    # DFU-02: busy=False, frozen=True, connected=False -> FROZEN
    assert records[1].busy is False
    assert records[1].frozen is True
    assert records[1].connected is False
    assert records[1].status == "FROZEN"

    # FCT-01: busy=False, frozen=False, connected=True -> IDLE
    assert records[2].busy is False
    assert records[2].frozen is False
    assert records[2].connected is True
    assert records[2].status == "IDLE"

    # BT-01: busy=True, frozen=True, connected=False -> BUSY
    assert records[3].busy is True
    assert records[3].frozen is True
    assert records[3].connected is False
    assert records[3].status == "BUSY"


# ---------------------------------------------------------------------------
# 2) Consistency test between core_status and status
# ---------------------------------------------------------------------------

def test_core_status_and_status_consistency():
    """Assert for every device, core_status station row is_busy/is_frozen/is_connected == status device busy/frozen/connected."""
    # Build a registry with a mix of devices
    custom_routes = {
        "DFU-01": {"station": "DFU", "kvm_ip": "192.168.200.1", "channels": 4},
        "DFU-02": {"station": "DFU", "kvm_ip": "192.168.200.2", "channels": 4},
        "DFU-03": {"station": "DFU", "kvm_ip": "192.168.200.3", "channels": 4},
        "DFU-04": {"station": "DFU", "kvm_ip": "192.168.200.4", "channels": 4},
        "DFU-05": {"station": "DFU", "kvm_ip": "192.168.200.5", "channels": 4},
        "DFU-06": {"station": "DFU", "kvm_ip": "192.168.200.6", "channels": 4},
        "FCT-01": {"station": "FCT", "kvm_ip": "192.168.201.1", "channels": 2},
        "BT-01": {"station": "BT", "kvm_ip": "192.168.202.1", "channels": 1},
    }
    registry = DeviceRegistry.from_text(json.dumps(custom_routes))
    server = TcpJsonServer(device_registry=registry, host="127.0.0.1", port=0)

    # Device states:
    # - DFU-01: busy + frozen + connected
    # - DFU-02: busy + frozen, disconnected
    # - DFU-03: frozen-only, connected
    # - DFU-04: frozen-only, disconnected
    # - DFU-05: busy-only, connected
    # - DFU-06: busy-only, disconnected
    # - FCT-01: connected-only
    # - BT-01: disconnected
    active_devices = ["DFU-01", "DFU-02", "DFU-05", "DFU-06"]
    frozen_devices = ["DFU-01", "DFU-02", "DFU-03", "DFU-04"]
    active_ips = ["192.168.200.1", "192.168.200.3", "192.168.201.1", "192.168.200.5"]

    server.lock_manager.get_active_devices = lambda: active_devices
    server.freeze_guard.get_status = lambda: {"frozen_devices": frozen_devices}
    server.kvm_pool.get_active_ips = lambda: active_ips

    ctx = CommandContext()
    core_res = asyncio.run(server._handle_core_status(ctx, None))
    status_res = asyncio.run(server._handle_status(ctx, None))

    stations_by_dev = {row["device"]: row for row in core_res["stations"]}
    devices_by_dev = {dev["device"]: dev for dev in status_res["devices"]}

    assert set(stations_by_dev.keys()) == set(devices_by_dev.keys()) == set(custom_routes.keys())

    for device, connected in (("DFU-05", True), ("DFU-06", False)):
        assert stations_by_dev[device]["status"] == "BUSY"
        assert stations_by_dev[device]["is_busy"] is devices_by_dev[device]["busy"] is True
        assert stations_by_dev[device]["is_frozen"] is devices_by_dev[device]["frozen"] is False
        assert stations_by_dev[device]["is_connected"] is devices_by_dev[device]["connected"] is connected

    for dev_id in custom_routes:
        st_row = stations_by_dev[dev_id]
        dev_row = devices_by_dev[dev_id]

        # Flags must be strictly identical across both endpoints
        assert st_row["is_busy"] == dev_row["busy"], f"busy mismatch for {dev_id}"
        assert st_row["is_frozen"] == dev_row["frozen"], f"frozen mismatch for {dev_id}"
        assert st_row["is_connected"] == dev_row["connected"], f"connected mismatch for {dev_id}"
        assert st_row["station"] == dev_row["station"]

        # KVM IPs must never appear in status devices
        assert "kvm_ip" not in dev_row
        assert "kvm_ip" in st_row

        # Check expected states for our mix
        if dev_id == "DFU-01":
            assert dev_row["busy"] is True and dev_row["frozen"] is True and dev_row["connected"] is True
            assert st_row["status"] == "BUSY"
        elif dev_id == "DFU-02":
            assert dev_row["busy"] is True and dev_row["frozen"] is True and dev_row["connected"] is False
            assert st_row["status"] == "BUSY"
        elif dev_id == "DFU-03":
            assert dev_row["busy"] is False and dev_row["frozen"] is True and dev_row["connected"] is True
            assert st_row["status"] == "FROZEN"
        elif dev_id == "DFU-04":
            assert dev_row["busy"] is False and dev_row["frozen"] is True and dev_row["connected"] is False
            assert st_row["status"] == "FROZEN"
        elif dev_id == "FCT-01":
            assert dev_row["busy"] is False and dev_row["frozen"] is False and dev_row["connected"] is True
            assert st_row["status"] == "IDLE"
        elif dev_id == "BT-01":
            assert dev_row["busy"] is False and dev_row["frozen"] is False and dev_row["connected"] is False
            assert st_row["status"] == "DISCONNECTED"


# ---------------------------------------------------------------------------
# 3) _device_health() calls getters once, takes no lock, reads no frame
# ---------------------------------------------------------------------------

def test_device_health_calls_getters_once_and_no_locks_or_frames():
    """_device_health() calls the 3 getters exactly once and never acquires locks or reads frames."""
    registry = make_registry()
    server = TcpJsonServer(device_registry=registry, host="127.0.0.1", port=0)

    tracked = server.freeze_guard.get_status()["tracked_devices_count"]
    slots, streams = len(server._slots), len(server.freeze_guard._streams)
    getters = [Mock(wraps=server.lock_manager.get_active_devices),
               Mock(wraps=server.freeze_guard.get_status),
               Mock(wraps=server.kvm_pool.get_active_ips)]
    server.lock_manager.get_active_devices, server.freeze_guard.get_status, server.kvm_pool.get_active_ips = getters

    def poison(*args, **kwargs):
        raise AssertionError("Status must not acquire a device lock or connection")

    server.lock_manager.try_acquire = poison
    server.kvm_pool.get_connection = poison
    server.kvm_pool.acquire_connection = poison

    class PoisonKVM:
        def latest_frame(self, *args, **kwargs):
            raise AssertionError("latest_frame called on KVM!")

    ip = registry.entries()[0].kvm_ip
    server.kvm_pool._pool[ip] = PooledKVMConnection(ip, PoisonKVM())
    server._device_health()
    for getter in getters:
        getter.assert_called_once_with()

    server._device_health = Mock(wraps=server._device_health)
    for handler in (server._handle_core_status, server._handle_status):
        calls_before = server._device_health.call_count
        asyncio.run(handler(CommandContext(), None))
        assert server._device_health.call_count == calls_before + 1
        assert len(server._slots) == slots
        assert len(server.freeze_guard._streams) == streams
        assert FrameFreezeGuard.get_status(server.freeze_guard)["tracked_devices_count"] == tracked


def test_health_records_are_stored_and_updated_without_tracking_streams():
    guard = FrameFreezeGuard()
    tracked = guard.get_status()["tracked_devices_count"]
    registry = make_registry()
    server = TcpJsonServer(device_registry=registry, freeze_guard=guard)
    assert guard.get_status()["tracked_devices_count"] == tracked
    assert list(server._health) == [entry.device_id for entry in registry.entries()]
    stored = dict(server._health)
    before = {h.device: h for h in server._device_health()}
    for device, record in stored.items():
        assert server._health[device] is record
        assert before[device] is not record
    first = registry.entries()[0]
    server.lock_manager.get_active_devices = lambda: [first.device_id]
    server.kvm_pool.get_active_ips = lambda: [first.kvm_ip]
    original_status = guard.get_status
    guard.get_status = lambda: {**original_status(), "frozen_devices": [first.device_id]}
    after = {h.device: h for h in server._device_health()}
    for device, record in stored.items():
        assert server._health[device] is record
        assert after[device] is not record
        assert after[device] is not before[device]
    record = stored[first.device_id]
    assert (record.busy, record.frozen, record.connected) == (True, True, True)
    after[first.device_id].kvm_ip = "10.0.0.99"
    refreshed = {h.device: h for h in server._device_health()}
    assert refreshed[first.device_id].kvm_ip == first.kvm_ip
    assert refreshed[first.device_id].connected is True
    server.lock_manager.get_active_devices = lambda: []
    server.kvm_pool.get_active_ips = lambda: []
    guard.get_status = original_status
    asyncio.run(server._handle_status(CommandContext(), None))
    asyncio.run(server._handle_core_status(CommandContext(), None))
    assert (record.busy, record.frozen, record.connected) == (False, False, False)
    assert guard.get_status()["tracked_devices_count"] == tracked
    assert not server._slots
    assert not guard._streams


def test_status_and_core_status_rows_keep_their_keys_in_order():
    """The prefactor must not change any external field: same keys, same order, same types."""
    registry = make_registry()
    server = TcpJsonServer(device_registry=registry, host="127.0.0.1", port=0)
    first = registry.entries()[0]
    server.kvm_pool.get_active_ips = lambda: [first.kvm_ip]

    rows = server._station_rows()
    assert list(rows[0])[:7] == ["station", "device", "kvm_ip", "status", "is_busy", "is_frozen", "is_connected"]
    assert type(rows[0]["status"]) is str
    status_res = asyncio.run(server._handle_status(CommandContext(), None))
    for dev, entry in zip(status_res["devices"], registry.entries()):
        assert list(dev) == ["device", "station", "connected", "busy", "frozen", "channels"]
        assert dev["channels"] == entry.channels
