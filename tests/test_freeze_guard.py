"""Automated tests for Frame Freeze Guard across all stations (BT, FCT, DFU).

Verifies that Presentation Timestamp (PTS) freeze guard:
1. Detects static timestamps across designated threshold window.
2. Returns {"status": "error", "error": "frame_frozen"} on freeze.
3. Allows live frames with normal timestamp progression to pass.
4. Protects all stations (BT, FCT, DFU) and integrates with TCP JSON server.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from fake_kvm import FakeKVM
from freeze_guard import FrameFreezeGuard
from round_frame_consumer import RoundFrameGate, observe_latest_round_frame
from protocol_helpers import LogSolutionKVM, arm_round, make_registry, wire, numbered, ProbeServer, recv_reply
from tcp_server import TcpJsonServer
from device_lock import DeviceLockManager
from kvm_pool import KVMConnectionPool


ROUTES = {
    "BT-01": {
        "station": "BT",
        "kvm_ip": "192.168.210.11",
        "channels": 4
    },
    "FCT-02": {
        "station": "FCT",
        "kvm_ip": "192.168.210.12",
        "channels": 4
    },
    "BT-03": {
        "station": "BT",
        "kvm_ip": "192.168.210.13",
        "channels": 4
    },
    "BT-10": {
        "station": "BT",
        "kvm_ip": "192.168.210.14",
        "channels": 4
    },
    "FCT-20": {
        "station": "FCT",
        "kvm_ip": "192.168.210.15",
        "channels": 4
    }
}


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

def send_json(sock: socket.socket, data: Dict[str, Any]) -> None:
    sock.sendall(dumps_request(wire(data)))


def recv_json(sock: socket.socket, timeout: float = 3.0) -> Dict[str, Any]:
    sock.settimeout(timeout)
    return recv_reply(sock)


# ---------------------------------------------------------------------------
# Station-Specific Tests: DFU RoundFrameGate Freeze Detection
# ---------------------------------------------------------------------------

class TestDFURoundFrameGateFreeze:
    def test_round_frame_gate_detects_frozen_pts(self):
        """RoundFrameGate must fail with reason='frame_frozen' when PTS remains static."""
        gate = RoundFrameGate("DFU-01", freeze_threshold=1.0, require_presentation_time=True)
        kvm = FakeKVM("frame", sequence=1, received_monotonic=0.0, stream_id="s", pts=10.0)

        dec1 = observe_latest_round_frame(kvm, gate, now=0.0)
        assert dec1.kind != "frozen"

        kvm.frame_sequence = 2
        kvm.frame_received_monotonic = 0.5
        dec2 = observe_latest_round_frame(kvm, gate, now=0.5)
        assert dec2.kind == "unknown"

        kvm.frame_sequence = 3
        kvm.frame_received_monotonic = 1.2
        dec3 = observe_latest_round_frame(kvm, gate, now=1.2)
        assert dec3.kind == "frozen"
        assert dec3.reason == "frame_frozen"
        assert gate.is_frozen is True

    def test_round_frame_gate_recovers_when_pts_resumes_advancing(self):
        gate = RoundFrameGate("DFU-01", freeze_threshold=1.0, require_presentation_time=True)
        gate.observe(None, sequence=1, received_at=0.0, now=0.0, pts=10.0)
        # Static -> frozen
        dec_frozen = gate.observe(None, sequence=2, received_at=1.2, now=1.2, pts=10.0)
        assert dec_frozen.kind == "frozen"

        # Stream recovers with advanced PTS
        dec_alive = gate.observe(None, sequence=3, received_at=1.3, now=1.3, pts=10.1)
        assert dec_alive.kind != "frozen"
        assert gate.is_frozen is False


# ---------------------------------------------------------------------------
# End-to-End Tests: Headless TCP JSON Server with Freeze Guard
# ---------------------------------------------------------------------------

@pytest.fixture
def freeze_rig():
    """Server with one synthetic Log Solution KVM per device IP and a short freeze threshold."""
    from protocol_helpers import LogSolutionKVM

    kvms: Dict[str, Any] = {}

    def client_factory(host: str, password: str = ""):
        return kvms.setdefault(host, LogSolutionKVM())

    server = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=client_factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.3),
    )
    server.start()
    time.sleep(0.05)
    host, port = server.server_address
    try:
        yield server, kvms, (host, port)
    finally:
        server.stop()
        server.wait(timeout=2.0)


def _check_on(address, device, req_id, timeout_sec):
    with socket.create_connection(address, timeout=10.0) as sock:
        send_json(sock, {"protocol_version": "1.0", "req_id": req_id, "device": device,
                         "cmd": "check_finish", "timeout_sec": timeout_sec})
        return recv_json(sock, timeout=10.0)


def _kvm_for(kvms, device):
    from protocol_helpers import LogSolutionKVM

    ip = make_registry().get(device).kvm_ip
    return kvms.setdefault(ip, LogSolutionKVM())


class TestTCPJsonServerFreezeGuard:
    @pytest.mark.parametrize("device", ["DFU-01", "FCT-01", "BT-01"])
    def test_check_frozen_picture_returns_frame_frozen_error(self, freeze_rig, device):
        server, kvms, address = freeze_rig
        kvm = _kvm_for(kvms, device)
        kvm.show("monitoring")
        kvm.freeze()

        res = _check_on(address, device, "req-chk-01", 5)

        assert res["req_id"] == "req-chk-01"
        assert res["status"] == "error"
        assert res["error"] == "frame_frozen"
        assert res["device"] == device

    def test_check_live_picture_is_not_reported_frozen(self, freeze_rig):
        server, kvms, address = freeze_rig
        arm_round(server, _kvm_for(kvms, "DFU-01"), "DFU-01")

        res = _check_on(address, "DFU-01", "req-chk-live", 0)

        assert res["status"] == "ok"
        assert res["data"]["kind"] == "waiting"

    def test_device_lock_released_after_freeze_error(self, freeze_rig):
        server, kvms, address = freeze_rig
        kvm = _kvm_for(kvms, "DFU-01")
        kvm.show("monitoring")
        kvm.freeze()
        assert _check_on(address, "DFU-01", "req-frz", 5)["error"] == "frame_frozen"

        followup = _check_on(address, "DFU-01", "req-after", 0)

        assert followup["error"] != "device_busy"
        assert not server.lock_manager.is_locked("DFU-01")

    def test_multi_device_isolation_frozen_vs_live(self, freeze_rig):
        server, kvms, address = freeze_rig
        frozen = _kvm_for(kvms, "DFU-01")
        frozen.show("monitoring")
        frozen.freeze()
        arm_round(server, _kvm_for(kvms, "DFU-02"), "DFU-02")

        frozen_res = _check_on(address, "DFU-01", "req-f", 5)
        live_res = _check_on(address, "DFU-02", "req-l", 0)

        assert frozen_res["error"] == "frame_frozen"
        assert live_res["status"] == "ok"
        assert live_res["data"]["kind"] == "waiting"

    def test_core_status_reports_frozen_device_only(self, freeze_rig):
        server, kvms, address = freeze_rig
        frozen = _kvm_for(kvms, "DFU-01")
        frozen.show("monitoring")
        frozen.freeze()
        _check_on(address, "DFU-01", "req-frz-st", 5)
        _kvm_for(kvms, "DFU-02").show("monitoring")

        with socket.create_connection(address, timeout=3.0) as sock:
            send_json(sock, {"protocol_version": "1.0", "req_id": "st", "device": "", "cmd": "status"})
            res = recv_json(sock, timeout=3.0)

        frozen_by_device = {d["device"]: d["frozen"] for d in res["data"]["devices"]}
        assert frozen_by_device["DFU-01"] is True
        assert frozen_by_device["DFU-02"] is False


# ---------------------------------------------------------------------------
# Stream reconnect: a new stream restarts the freeze state of its device (#18)
# ---------------------------------------------------------------------------

class _ReconnectingKVM(LogSolutionKVM):
    """Log Solution fake whose stream can be replaced, as after a JetKVM reconnect."""

    def __init__(self):
        super().__init__(state="standby")
        self._stream = "stream-1"

    def reconnect(self):
        with self._lock:
            self._stream = "stream-2"
            self._pts = 1.0  # the new stream's PTS restarts from a low value

    def latest_frame(self):
        return dataclasses.replace(super().latest_frame(), stream_id=self._stream)


class TestStreamReconnectOverTheWire:
    def test_reconnected_stream_does_not_report_frame_frozen(self, freeze_rig):
        server, kvms, address = freeze_rig
        kvm = _ReconnectingKVM()
        kvms[make_registry().get("DFU-01").kvm_ip] = kvm

        assert _check_on(address, "DFU-01", "old-1", 0).get("error") != "frame_frozen"
        time.sleep(0.5)  # last advance on the old stream is older than the 0.3 s threshold
        kvm.reconnect()

        resp = _check_on(address, "DFU-01", "new-1", 0)
        assert resp.get("error") != "frame_frozen", resp


if __name__ == "__main__":
    pytest.main(["-v", __file__])
