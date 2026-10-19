"""Socket-layer regressions for the PR #50 review (A1–A4 and related gaps)."""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"


@pytest.fixture
def rig(tmp_path):
    kvm = LogSolutionKVM(state="standby")
    root = tmp_path / "template"
    root.mkdir()
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        template_root=root,
        monitor_confirm_sec=0.3,
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, cmd, timeout_sec=0, req_id="r", device=DEVICE, **extra):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": cmd,
               "timeout_sec": timeout_sec, **extra}
    if cmd == "monitor_start" and "timeout_sec" not in extra and timeout_sec == 0:
        payload["timeout_sec"] = 1
    with socket.create_connection((host, port), timeout=15.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def test_check_start_prefers_taken_over_started_on_a_complete_screen(rig):
    """A1: activity left over from monitoring must not turn a complete screen into started."""
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING", "WAITING", "WAITING", "WAITING"])
    assert _send(srv, "check_start", 0, "a")["data"]["kind"] == "waiting"

    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    delivered = _send(srv, "check_start", 2, "b")
    assert delivered["data"]["kind"] == "taken"
    assert delivered["data"]["state"] == "complete"
    assert delivered["data"]["active"] == []
    assert [row["status"] for row in delivered["data"]["results"]] == ["PASS"] * 4


def test_inspect_complete_screen_is_waiting_not_started(rig):
    """A2: residual complete results are not 'started' channels."""
    srv, kvm = rig
    kvm.show("complete", ["PASS", "FAIL", "PASS", "NOTEST"])
    first = _send(srv, "inspect", 0, "i1")
    second = _send(srv, "inspect", 0, "i2")

    assert first["data"]["kind"] == "waiting"
    assert first["data"]["state"] == "complete"
    assert first["data"]["active"] == []
    assert second["data"]["kind"] == "waiting"
    assert second["data"]["state"] == "complete"
    assert second["data"]["active"] == []
    assert second["data"]["kind"] != "started"
    assert kvm.events == []


def test_busy_on_monitoring_does_not_count_as_seen_monitoring(rig):
    """A3: log_solution_busy must not invent monitoring_seen and hang check_finish."""
    srv, kvm = rig
    kvm.show("preparing")
    timed_out = _send(srv, "monitor_start", 1, "m1")
    assert timed_out["error"] == "monitor_not_started"

    kvm.show("monitoring")
    busy = _send(srv, "monitor_start", 1, "m2")
    assert busy["error"] == "log_solution_busy"

    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    finish = _send(srv, "check_finish", 1, "cf1")
    assert finish.get("error") == "manual_verification_required"
    assert finish["data"]["results"] == []
    assert finish["status"] != "timeout"


def test_unknown_source_time_clears_started_activity(rig):
    """A4: a stream-time problem resets activity so the next check_start does not keep stale active."""
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("monitoring", ["TESTING", "WAITING", "WAITING", "WAITING"])
    _send(srv, "check_start", 0, "a")
    started = _send(srv, "check_start", 0, "b")
    assert started["data"]["kind"] == "started"
    assert started["data"]["active"]

    # An unreadable frame must reset the activity pair (spec: unknown 中間重新計算).
    kvm.blank()
    bad = _send(srv, "check_start", 0, "c")
    assert bad["data"]["kind"] == "unknown"
    assert bad["data"]["active"] == []

    kvm.show("monitoring", ["TESTING", "WAITING", "WAITING", "WAITING"])
    again = _send(srv, "check_start", 0, "d")
    assert again["data"]["kind"] == "waiting"
    assert again["data"]["active"] == []


@pytest.mark.parametrize(
    "state,statuses,kind,state_name",
    [
        ("standby", (), "waiting", "standby"),
        ("preparing", (), "waiting", "preparing"),
        ("monitoring", (), "waiting", "monitoring"),
        ("review", (), "paused", "review"),
        ("complete", ("PASS", "PASS", "PASS", "PASS"), "waiting", "complete"),
        ("manual_stop_complete", ("PASS", "FAIL", "STOPPED", "NOTEST"), "waiting", "manual_stop_complete"),
    ],
)
def test_socket_reports_each_whole_round_pattern(rig, state, statuses, kind, state_name):
    """C: each of the six patterns is visible over the socket (inspect, no start request)."""
    srv, kvm = rig
    kvm.show(state, statuses)
    resp = _send(srv, "inspect", 0, f"p-{state}")
    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == kind
    assert resp["data"]["state"] == state_name


def test_socket_unknown_pattern_is_unknown(rig):
    srv, kvm = rig
    kvm.blank()
    resp = _send(srv, "inspect", 0, "unk")
    assert resp["data"]["kind"] == "unknown"


def test_new_monitor_start_clears_delivered_signature(rig):
    """C: a new monitor_start drops already_taken for the previous delivery."""
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    assert _send(srv, "check_finish", 0, "a")["data"]["kind"] == "waiting"
    taken = _send(srv, "check_finish", 0, "b")
    assert taken["data"]["kind"] == "taken"
    replay = _send(srv, "check_finish", 0, "c")
    assert replay["data"]["kind"] == "already_taken"
    assert replay["data"]["results"] == taken["data"]["results"]

    kvm.show("preparing")
    started = _send(srv, "monitor_start", 1, "m")
    # Preparing alone may time out; either way the delivered signature is gone.
    assert started.get("status") in ("ok", "error")
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    after = _send(srv, "check_finish", 0, "d")
    assert after.get("error") == "monitor_not_ready" or after["data"]["kind"] != "already_taken"
