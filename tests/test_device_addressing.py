"""Socket-level tests for device-ID addressing (protocol 1.0, ticket #2).

Covers snapshot resolved by device ID through the device registry, the
forbidden/unknown/missing device errors, and Core refusing to start when the
device list is missing or invalid.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Ensure host-app is on sys.path
_HOST_APP_DIR = Path(__file__).resolve().parents[1] / "host-app"
sys.path.insert(0, str(_HOST_APP_DIR))

from device_registry import DeviceRegistry
from fake_kvm import FakeKVM as SharedFakeKVM
from kvm_pool import KVMConnectionPool
from protocol_helpers import wire
from tcp_server import TcpJsonServer


DEVICES = {
    "DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4},
    "FCT-01": {"station": "FCT", "kvm_ip": "192.168.132.77", "channels": 4},
    "BT-01": {"station": "BT", "kvm_ip": "192.168.132.78", "channels": 4},
}


class FakeKVM(SharedFakeKVM):
    """Minimal KVM client exposing one frame to snapshot."""

    def __init__(self) -> None:
        super().__init__(b"frame-bytes", pts=12.5)


@pytest.fixture
def running_server():
    kvm_hosts: list[str] = []

    def client_factory(host: str, password: str = ""):
        kvm_hosts.append(host)
        return FakeKVM()

    registry = DeviceRegistry.from_text(json.dumps(DEVICES))
    pool = KVMConnectionPool(client_factory=client_factory)
    server = TcpJsonServer(host="127.0.0.1", port=0, kvm_pool=pool, device_registry=registry)
    server.start()
    time.sleep(0.05)
    server.kvm_hosts = kvm_hosts
    try:
        yield server
    finally:
        server.stop()


def _roundtrip(server, payload: dict) -> dict:
    host, port = server.server_address
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.sendall((json.dumps(wire(payload)) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def test_snapshot_by_device_id_connects_to_registry_ip(running_server):
    resp = _roundtrip(running_server, {"req_id": "s1", "cmd": "snapshot", "device": "DFU-01", "format": "jpeg", "quality": 80})

    assert resp["status"] == "ok"
    assert resp["device"] == "DFU-01"
    assert resp["data"]["image_base64"] != ""
    assert running_server.kvm_hosts == ["192.168.132.70"]


def test_snapshot_rejects_kvm_ip(running_server):
    resp = _roundtrip(running_server, {"req_id": "s2", "cmd": "snapshot", "device": "DFU-01", "kvm_ip": "192.168.132.70"})

    assert resp["status"] == "error"
    assert resp["error"] == "forbidden_field"
    assert running_server.kvm_hosts == []


def test_snapshot_rejects_station(running_server):
    resp = _roundtrip(running_server, {"req_id": "s3", "cmd": "snapshot", "device": "DFU-01", "station": "DFU"})

    assert resp["error"] == "forbidden_field"
    assert running_server.kvm_hosts == []


def test_snapshot_unknown_device_is_rejected_case_sensitively(running_server):
    resp = _roundtrip(running_server, {"req_id": "s4", "cmd": "snapshot", "device": "dfu-01"})

    assert resp["status"] == "error"
    assert resp["error"] == "unknown_device"
    assert running_server.kvm_hosts == []


def test_snapshot_without_device_is_rejected(running_server):
    resp = _roundtrip(running_server, {"req_id": "s5", "cmd": "snapshot"})

    assert resp["status"] == "error"
    assert resp["error"] == "missing_device"


def test_different_stations_do_not_share_state(running_server):
    first = _roundtrip(running_server, {"req_id": "s6", "cmd": "snapshot", "device": "FCT-01",
                                        "format": "jpeg", "quality": 80})
    second = _roundtrip(running_server, {"req_id": "s7", "cmd": "snapshot", "device": "BT-01",
                                         "format": "jpeg", "quality": 80})

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert running_server.kvm_hosts == ["192.168.132.77", "192.168.132.78"]


def test_core_refuses_to_start_when_device_list_missing(tmp_path):
    missing = tmp_path / "devices.json"

    proc = subprocess.run(
        [sys.executable, str(_HOST_APP_DIR / "tcp_server.py"), "--host", "127.0.0.1", "--port", "0", "--devices-file", str(missing)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode != 0
    assert "device list not found" in proc.stderr


def test_core_refuses_to_start_and_lists_errors_when_device_list_invalid(tmp_path):
    bad = tmp_path / "devices.json"
    bad.write_text(json.dumps({"DFU-1": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4}}), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(_HOST_APP_DIR / "tcp_server.py"), "--host", "127.0.0.1", "--port", "0", "--devices-file", str(bad)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode != 0
    assert "DFU-1" in proc.stderr
