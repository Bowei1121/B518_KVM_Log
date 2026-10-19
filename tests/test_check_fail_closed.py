"""check_finish must fail closed on every station: no positively observed result means no taken.

Socket and synthetic Log Solution frames only. No private-state setup.
"""
import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

ALL_STATIONS = ["DFU-01", "FCT-01", "BT-01"]


@pytest.fixture
def rig():
    registry = make_registry()
    kvms = {}
    for device in ALL_STATIONS:
        kvms[device] = LogSolutionKVM(state="standby")
    by_ip = {registry.get(device).kvm_ip: kvms[device] for device in ALL_STATIONS}

    def client_factory(host, password=""):
        return by_ip.setdefault(host, LogSolutionKVM())

    srv = TcpJsonServer(
        device_registry=registry,
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=client_factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.05),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvms
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _check(srv, device, timeout_sec=0, req_id="fc"):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device,
               "cmd": "check_finish", "timeout_sec": timeout_sec}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_check_without_log_solution_frame_is_never_taken_from_templates(rig, device):
    srv, kvms = rig
    kvms[device].blank()
    res = _check(srv, device)
    assert res["data"]["kind"] != "taken"
    assert res["data"]["results"] == []


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_no_frame_yet_is_error_no_result(rig, device):
    srv, kvms = rig
    kvm = kvms[device]
    pts = {"v": 100.0}

    def no_frame():
        pts["v"] += 1.0
        return (None, int(pts["v"]), time.monotonic(), "synthetic-stream", pts["v"])

    kvm.latest_frame = no_frame  # type: ignore[method-assign]
    res = _check(srv, device)
    assert res["status"] == "error"
    assert res["error"] == "no_frame"
    assert res["data"]["results"] == []


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_monitoring_without_complete_is_waiting_not_taken(rig, device):
    srv, kvms = rig
    arm_round(srv, kvms[device], device)
    kvms[device].show("monitoring")
    res = _check(srv, device)
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "waiting"
    assert res["data"]["results"] == []


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_review_is_paused_not_taken(rig, device):
    srv, kvms = rig
    arm_round(srv, kvms[device], device)
    kvms[device].show("review")
    res = _check(srv, device)
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "paused"
    assert res["data"]["results"] == []


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_blank_screen_is_unknown_not_taken(rig, device):
    srv, kvms = rig
    arm_round(srv, kvms[device], device)
    kvms[device].blank()
    res = _check(srv, device)
    assert res["data"]["kind"] == "unknown"
    assert res["data"]["results"] == []


@pytest.mark.parametrize(
    "statuses",
    [("PASS", "PASS", "PASS", "PASS"), ("PASS", "FAIL", "PASS", "PASS"),
     ("PASS", "NOTEST", "PASS", "PASS"), ("TIMEOUT", "PASS", "PASS", "PASS"),
     ("STOPPED", "PASS", "PASS", "PASS")],
)
@pytest.mark.parametrize("device", ALL_STATIONS)
def test_taken_keeps_each_slot_status_and_never_aggregates(rig, statuses, device):
    srv, kvms = rig
    arm_round(srv, kvms[device], device)
    kvms[device].show("complete", list(statuses))
    assert _check(srv, device, req_id="a")["data"]["kind"] == "waiting"
    res = _check(srv, device, req_id="b")
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "taken"
    assert res["data"]["results"] == [{"slot": i, "status": s} for i, s in enumerate(statuses, 1)]
    assert "result" not in res["data"]


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_frozen_stream_is_frame_frozen_error(rig, device):
    srv, kvms = rig
    arm_round(srv, kvms[device], device)
    kvms[device].show("monitoring")
    _check(srv, device, req_id="live")
    kvms[device].freeze()
    time.sleep(0.08)
    res = _check(srv, device, req_id="frozen")
    assert res["status"] == "error"
    assert res["error"] == "frame_frozen"
    assert res["data"]["kind"] != "taken"


class _BoomAfterArmKVM(LogSolutionKVM):
    """latest_frame raises after explode(); freeze guard still advances via attributes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._boom = False
        self._attr_pts = 9000.0
        self._attr_seq = 1

    def explode(self):
        self._boom = True

    def latest_frame(self):
        if self._boom:
            raise RuntimeError("decode failed")
        return LogSolutionKVM.latest_frame(self)

    @property
    def frame_presentation_time(self):
        self._attr_pts += 0.05
        return self._attr_pts

    @property
    def frame_sequence(self):
        self._attr_seq += 1
        return self._attr_seq

    @property
    def stream_id(self):
        return "synthetic-stream"


@pytest.fixture
def boom_rig():
    """One exploding KVM per station IP, for internal_error fail-closed cases."""
    registry = make_registry()
    kvms = {device: _BoomAfterArmKVM(state="standby") for device in ALL_STATIONS}
    by_ip = {registry.get(device).kvm_ip: kvms[device] for device in ALL_STATIONS}

    def client_factory(host, password=""):
        return by_ip.setdefault(host, _BoomAfterArmKVM())

    srv = TcpJsonServer(
        device_registry=registry,
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=client_factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.05),
        monitor_confirm_sec=0.3,
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvms
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _round_cmd(srv, device, cmd, timeout_sec=0, req_id="ie"):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device,
               "cmd": cmd, "timeout_sec": timeout_sec}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


@pytest.mark.parametrize("cmd", ["check_finish", "check_start"])
@pytest.mark.parametrize("timeout_sec", [0, 1])
@pytest.mark.parametrize("device", ALL_STATIONS)
def test_inspection_exception_is_internal_error(boom_rig, cmd, timeout_sec, device):
    """Decode failure during observation must never deliver a result (PR #14 fail-closed)."""
    srv, kvms = boom_rig
    kvm = kvms[device]
    arm_round(srv, kvm, device)
    hid_before = list(kvm.events)
    kvm.explode()

    res = _round_cmd(srv, device, cmd, timeout_sec=timeout_sec, req_id=f"{cmd}-{timeout_sec}")

    assert res["status"] == "error"
    assert res["error"] == "internal_error"
    assert res["data"]["kind"] == "unknown"
    assert res["data"]["results"] == []
    assert res["data"]["kind"] != "taken"
    assert kvm.events == hid_before
