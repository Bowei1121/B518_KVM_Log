"""check_start confirms a round has started (ticket #45).

Two fresh frames, sequence advancing and at most one second apart, must agree on a
slot before it is active. TESTING/COMPLETING are evidence testing. PASS/FAIL seen
without testing are evidence result. The command can deliver a finished round and
does not consume an in-progress one.
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
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"


@pytest.fixture
def rig():
    kvm = LogSolutionKVM(state="standby")
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, cmd, timeout_sec=0, req_id="cs", device=DEVICE):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": cmd,
               "timeout_sec": timeout_sec}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _start(srv, timeout_sec=0, req_id="cs"):
    return _send(srv, "check_start", timeout_sec, req_id)


def test_check_start_requires_timeout_sec(rig):
    srv, _kvm = rig
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": "bad", "device": DEVICE, "cmd": "check_start"}
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        resp = json.loads(sock.makefile().readline())
    assert resp["error"] == "invalid_argument"


def test_two_frames_intersect_and_classify_evidence(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING", "PASS", "WAITING", "NOTEST"])
    first = _start(srv, req_id="one")
    kvm.show("monitoring", ["PASS", "FAIL", "COMPLETING", "TIMEOUT"])
    second = _start(srv, req_id="two")

    assert first["status"] == "ok"
    assert first["data"]["kind"] == "waiting"
    assert first["data"]["active"] == []
    assert second["data"]["kind"] == "started"
    # Slot 1 was testing then pass: testing wins. Slot 2 is result on both.
    # Slot 3 was idle then completing. Slot 4 is never activity.
    assert second["data"]["active"] == [
        {"slot": 1, "evidence": "testing"},
        {"slot": 2, "evidence": "result"},
    ]
    assert second["data"]["results"] == []


def test_stopped_timeout_and_waiting_are_not_activity(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["STOPPED", "TIMEOUT", "WAITING", "NOTEST"])
    _start(srv, req_id="a")
    resp = _start(srv, req_id="b")

    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["active"] == []


def test_unknown_frame_resets_the_activity_pair(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING"] * 4)
    _start(srv, req_id="a")
    kvm.blank()
    assert _start(srv, req_id="blank")["data"]["kind"] == "unknown"
    kvm.show("monitoring", ["TESTING"] * 4)
    resp = _start(srv, req_id="b")

    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["active"] == []


def test_frames_more_than_a_second_apart_do_not_pair(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING"] * 4)
    _start(srv, req_id="a")
    time.sleep(1.1)
    resp = _start(srv, req_id="b")

    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["active"] == []


def test_finished_screen_is_taken_and_check_finish_replays_it(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
    assert _start(srv, req_id="a")["data"]["kind"] == "waiting"
    taken = _start(srv, req_id="b")
    replay = _send(srv, "check_finish", req_id="finish")

    assert taken["data"]["kind"] == "taken"
    assert taken["data"]["state"] == "complete"
    assert [row["status"] for row in taken["data"]["results"]] == ["PASS", "FAIL", "NOTEST", "TIMEOUT"]
    assert replay["data"]["kind"] == "already_taken"
    assert replay["data"]["results"] == taken["data"]["results"]


def test_repeated_check_start_does_not_consume_the_round(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING", "WAITING", "WAITING", "WAITING"])
    _start(srv, req_id="a")
    assert _start(srv, req_id="b")["data"]["kind"] == "started"
    again = _start(srv, req_id="c")
    assert again["data"]["kind"] == "started"
    assert again["data"]["active"] == [{"slot": 1, "evidence": "testing"}]

    kvm.show("complete", ["PASS", "FAIL", "PASS", "PASS"])
    assert _send(srv, "check_finish", req_id="f1")["data"]["kind"] == "waiting"
    finish = _send(srv, "check_finish", req_id="f2")
    assert finish["data"]["kind"] == "taken"
    assert finish["data"]["active"] == []


def test_check_start_without_a_request_is_monitor_not_ready(rig):
    srv, kvm = rig
    kvm.show("monitoring", ["TESTING"] * 4)
    _start(srv, req_id="a")
    resp = _start(srv, req_id="b")

    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["results"] == []
    assert resp["data"]["kind"] != "started"


def test_check_start_review_is_paused_and_holds_the_lock(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring")
    holder = {}

    def run():
        holder["resp"] = _start(srv, timeout_sec=5, req_id="hold")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.3)
    assert srv.lock_manager.is_locked(DEVICE)
    busy = _start(srv, req_id="busy")
    kvm.show("review")
    thread.join(timeout=5)

    assert busy["error"] == "device_busy"
    assert holder["resp"]["data"]["kind"] == "paused"
    assert holder["resp"]["data"]["state"] == "review"


def test_inspect_reports_started_channels_and_never_clicks(rig):
    srv, kvm = rig
    kvm.show("monitoring", ["COMPLETING", "FAIL", "WAITING", "WAITING"])
    first = _send(srv, "inspect", req_id="i1")
    second = _send(srv, "inspect", req_id="i2")

    assert first["data"]["kind"] == "waiting"
    assert first["data"]["active"] == []
    assert second["data"]["kind"] == "started"
    assert second["data"]["active"] == [
        {"slot": 1, "evidence": "testing"},
        {"slot": 2, "evidence": "result"},
    ]
    assert second["data"]["kind"] != "taken"
    assert kvm.events == []
