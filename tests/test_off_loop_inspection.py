"""Frame readings run off the event loop with a per-frame time limit (ticket #6).

A deliberately slow reading on one device must not delay another device's check or
ping, and must be reported as unknown (never as a result) once it passes the limit.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

import round_frame_consumer  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

SLOW_DEVICE = "DFU-01"
FAST_DEVICE = "DFU-02"
SLOW_SECONDS = 1.0


@pytest.fixture
def rig(monkeypatch):
    original = round_frame_consumer.observe_latest_round_frame

    def slow_for_one_device(kvm, gate, accept_round=True):
        if gate.device_id == SLOW_DEVICE:
            time.sleep(SLOW_SECONDS)
        return original(kvm, gate, accept_round=accept_round)

    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", slow_for_one_device)

    kvms = {}

    def factory(host, password=""):
        return kvms.setdefault(host, LogSolutionKVM())

    from kvm_pool import KVMConnectionPool

    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        inspection_timeout_sec=0.2,
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvms
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _kvm(kvms, device):
    return kvms.setdefault(make_registry().get(device).kvm_ip, LogSolutionKVM())


def _call(srv, payload):
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request({"protocol_version": "1.0", **payload}))
        return recv_reply(sock)


def test_slow_reading_is_reported_unknown_within_the_frame_limit(rig):
    srv, kvms = rig
    _kvm(kvms, SLOW_DEVICE).show("monitoring")

    started = time.monotonic()
    resp = _call(srv, {"req_id": "slow-1", "device": SLOW_DEVICE, "cmd": "check_finish", "timeout_sec": 0})
    elapsed = time.monotonic() - started

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "unknown"
    assert resp["data"]["reason"] == "inspection_timeout"
    assert resp["data"]["results"] == []
    assert elapsed < SLOW_SECONDS


def test_slow_device_does_not_delay_another_devices_check_or_ping(rig):
    srv, kvms = rig
    _kvm(kvms, SLOW_DEVICE).show("monitoring")
    arm_round(srv, _kvm(kvms, FAST_DEVICE), FAST_DEVICE)
    background = {}

    def slow_long_poll():
        background["resp"] = _call(srv, {"req_id": "slow-lp", "device": SLOW_DEVICE, "cmd": "check_finish",
                                         "timeout_sec": 2.5})

    th = threading.Thread(target=slow_long_poll, daemon=True)
    th.start()
    time.sleep(0.3)

    started = time.monotonic()
    fast_check = _call(srv, {"req_id": "fast-chk", "device": FAST_DEVICE, "cmd": "check_finish", "timeout_sec": 0})
    fast_check_elapsed = time.monotonic() - started
    started = time.monotonic()
    pong = _call(srv, {"req_id": "ping-1", "device": "", "cmd": "ping"})
    ping_elapsed = time.monotonic() - started
    th.join(timeout=10)

    assert fast_check["data"]["kind"] == "waiting"
    assert fast_check_elapsed < 0.3
    assert pong["data"]["pong"] is True
    assert ping_elapsed < 0.3
    assert background["resp"]["status"] == "timeout"


def test_slow_reading_never_produces_a_taken_result(rig):
    srv, kvms = rig
    slow_kvm = _kvm(kvms, SLOW_DEVICE)
    slow_kvm.show("monitoring")
    holder = {}

    def long_poll():
        holder["resp"] = _call(srv, {"req_id": "slow-taken", "device": SLOW_DEVICE, "cmd": "check_finish",
                                     "timeout_sec": 2.0})

    th = threading.Thread(target=long_poll, daemon=True)
    th.start()
    time.sleep(0.3)
    slow_kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    th.join(timeout=10)

    resp = holder["resp"]
    assert resp["status"] == "timeout"
    assert resp["data"]["kind"] != "taken"
    assert resp["data"]["results"] == []
    assert resp["data"]["reason"] in ("inspection_timeout", "inspection_busy")
