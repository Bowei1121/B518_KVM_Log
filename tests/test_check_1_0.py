"""Socket-level tests for check 1.0 (ticket #5) against synthetic Log Solution screens.

The fake KVM renders contract 1.1 frames; the device is a DFU device so the round gate
(monitoring, then a stable complete screen) decides the result.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"
DATA_KEYS = {"kind", "state", "reason", "capacity", "results"}


@pytest.fixture
def rig():
    kvm = LogSolutionKVM(state="standby")
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.3),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()


def _check(srv, timeout_sec, req_id="c1", device=DEVICE, **extra):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "check",
               "timeout_sec": timeout_sec, **extra}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _in_background(fn):
    out = {}

    def run():
        out["resp"] = fn()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def _settle(seconds=0.7):
    # Long enough for at least one 0.5 s poll to observe the state just set.
    time.sleep(seconds)


def test_single_read_on_standby_returns_waiting_without_results(rig):
    srv, kvm = rig
    kvm.show("standby")

    resp = _check(srv, 0)

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert set(resp["data"]) == DATA_KEYS
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "standby"
    assert resp["data"]["results"] == []


def test_single_read_while_monitoring_is_waiting(rig):
    srv, kvm = rig
    kvm.show("monitoring")

    resp = _check(srv, 0)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "monitoring"
    assert resp["data"]["capacity"] == 4


def test_blank_screen_is_unknown_and_never_a_product_result(rig):
    srv, kvm = rig
    kvm.blank()

    resp = _check(srv, 0)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "unknown"
    assert resp["data"]["results"] == []


def test_long_poll_returns_taken_per_slot_as_soon_as_complete_is_seen(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    th, out = _in_background(lambda: _check(srv, 10))
    _settle()
    started = time.monotonic()
    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "PASS"])
    th.join(timeout=10)

    resp = out["resp"]
    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert set(resp["data"]) == DATA_KEYS
    assert resp["data"]["kind"] == "taken"
    assert resp["data"]["state"] == "complete"
    assert resp["data"]["reason"] == "reliable_complete"
    assert resp["data"]["capacity"] == 4
    assert resp["data"]["results"] == [
        {"slot": 1, "status": "PASS"}, {"slot": 2, "status": "FAIL"},
        {"slot": 3, "status": "NOTEST"}, {"slot": 4, "status": "PASS"},
    ]
    assert "result" not in resp["data"]
    assert time.monotonic() - started < 5.0


def test_stopped_slots_are_returned_as_is(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    th, out = _in_background(lambda: _check(srv, 10))
    _settle()
    kvm.show("complete", ["PASS", "STOPPED", "PASS", "PASS"])
    th.join(timeout=10)

    resp = out["resp"]
    assert resp["data"]["kind"] == "taken"
    assert {"slot": 2, "status": "STOPPED"} in resp["data"]["results"]


def test_completing_slot_is_still_waiting_not_taken(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    _settle(0.2)
    kvm.show("complete", ["PASS", "COMPLETING", "PASS", "PASS"])

    resp = _check(srv, 0)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["results"] == []


def test_review_pauses_immediately_during_long_poll(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    th, out = _in_background(lambda: _check(srv, 10))
    _settle()
    started = time.monotonic()
    kvm.show("review")
    th.join(timeout=10)

    resp = out["resp"]
    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "paused"
    assert resp["data"]["state"] == "review"
    assert resp["data"]["reason"] == "confirmation_required"
    assert time.monotonic() - started < 3.0


def test_second_read_after_taken_is_already_taken(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    th, out = _in_background(lambda: _check(srv, 10, req_id="first"))
    _settle()
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    th.join(timeout=10)
    assert out["resp"]["data"]["kind"] == "taken"

    resp = _check(srv, 0, req_id="second")

    assert resp["data"]["kind"] == "already_taken"
    assert resp["data"]["results"] == []


def test_long_poll_times_out_with_last_observed_state(rig):
    srv, kvm = rig
    kvm.show("monitoring")

    resp = _check(srv, 1.0)

    assert resp["status"] == "timeout"
    assert resp["error"] == "timeout"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "monitoring"
    assert resp["data"]["results"] == []


def test_capacity_mismatch_never_delivers_results(rig):
    srv, kvm = rig
    kvm.show("monitoring", ["WAITING"] * 2)  # a two-slot round on a four-channel device
    th, out = _in_background(lambda: _check(srv, 10))
    _settle()
    kvm.show("complete", ["PASS", "FAIL"])
    th.join(timeout=10)

    resp = out["resp"]
    assert resp["status"] == "error"
    assert resp["error"] == "capacity_mismatch"
    assert resp["data"]["results"] == []
    assert resp["data"]["kind"] != "taken"


@pytest.mark.parametrize("timeout_sec", [-1, 3601, "soon", None, True])
def test_timeout_out_of_range_or_wrong_type_is_invalid_argument(rig, timeout_sec):
    srv, _ = rig
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": "bad", "device": DEVICE, "cmd": "check"}
    if timeout_sec is not None:
        payload["timeout_sec"] = timeout_sec
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        resp = json.loads(sock.makefile().readline())

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"


def test_frozen_picture_during_long_poll_is_frame_frozen(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    kvm.freeze()

    resp = _check(srv, 5)

    assert resp["status"] == "error"
    assert resp["error"] == "frame_frozen"


def test_taken_is_reported_once_even_if_screen_keeps_complete(rig):
    srv, kvm = rig
    kvm.show("monitoring")
    th, out = _in_background(lambda: _check(srv, 10))
    _settle()
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    th.join(timeout=10)

    assert out["resp"]["data"]["kind"] == "taken"
    assert _check(srv, 0, req_id="again")["data"]["kind"] == "already_taken"
