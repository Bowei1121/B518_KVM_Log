"""Socket-level tests for device-ID-only identity (ticket #4).

Device locks, frozen state and KVM connections are all keyed by device ID; KVM IP and
station always come from the device list, never from the request.
"""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import ProbeServer, make_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402


def _send(srv, obj: dict) -> dict:
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _probe(device: str, req_id: str) -> dict:
    return {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "probe"}


@pytest.fixture
def probe_server():
    srv = ProbeServer(device_registry=make_registry(), host="127.0.0.1", port=0, kvm_pool=make_pool())
    srv.start()
    time.sleep(0.05)
    try:
        yield srv
    finally:
        srv.stop()


def test_devices_on_different_stations_run_without_busy(probe_server):
    assert probe_server.lock_manager.try_acquire("DFU-01", req_id="held") is True

    for device in ("FCT-01", "BT-01"):
        resp = _send(probe_server, _probe(device, f"x-{device}"))
        assert resp["status"] == "ok", resp


def test_same_station_devices_do_not_share_busy_state(probe_server):
    assert probe_server.lock_manager.try_acquire("BT-01", req_id="held") is True

    resp = _send(probe_server, _probe("BT-02", "bt2"))

    assert resp["status"] == "ok"
    assert resp["device"] == "BT-02"


def test_second_command_on_same_device_is_device_busy(probe_server):
    assert probe_server.lock_manager.try_acquire("DFU-01", req_id="held") is True

    resp = _send(probe_server, _probe("DFU-01", "again"))

    assert resp["status"] == "error"
    assert resp["error"] == "device_busy"


def test_status_reports_busy_per_device_only(probe_server):
    assert probe_server.lock_manager.try_acquire("BT-01", req_id="held") is True

    resp = _send(probe_server, {"protocol_version": "1.0", "req_id": "st", "device": "", "cmd": "status"})
    busy = {d["device"]: d["busy"] for d in resp["data"]["devices"]}

    assert busy["BT-01"] is True
    assert busy["BT-02"] is False
    assert busy["DFU-01"] is False


def test_check_rejects_raw_kvm_ip_and_station(probe_server):
    for field, value in (("kvm_ip", "192.168.1.99"), ("station", "DFU")):
        resp = _send(probe_server, {
            "protocol_version": "1.0", "req_id": f"chk-{field}", "device": "BT-01",
            "cmd": "check", "timeout_sec": 0, field: value,
        })
        assert resp["error"] == "forbidden_field"


def test_check_connects_to_the_registry_ip_for_the_device():
    pool = KVMConnectionPool(client_factory=lambda host, password="": _Unreachable(host))
    srv = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0, kvm_pool=pool)
    srv.start()
    time.sleep(0.05)
    try:
        resp = _send(srv, {"protocol_version": "1.0", "req_id": "c-ip", "device": "BT-01",
                           "cmd": "check", "timeout_sec": 0})
        assert resp["error"] == "kvm_connect_failed"
        assert "192.168.202.1" in resp["message"]
    finally:
        srv.stop()


class _Unreachable:
    def __init__(self, host):
        self.host = host
        self.connected = False

    async def connect(self):
        raise ConnectionError(f"unreachable {self.host}")
