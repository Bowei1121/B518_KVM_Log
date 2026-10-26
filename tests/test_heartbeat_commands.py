"""Log Solution heartbeat over the socket (issue #77): a stopped display is refused before any HID.

The picture keeps arriving with PTS moving, so FrameStream sees a live stream. Only the four heartbeat cells
hold still, and each command must answer log_solution_heartbeat_stopped. input, start_test and monitor_start
send no HID at all. check_start, check_finish and inspect answer the same error at once, mid long-poll.
"""

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_protocol import build_request, dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from dfu_helpers import SyntheticDfuKVM, save_templates  # noqa: E402
from fake_kvm import beside, make_snapshot  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from log_solution_heartbeat import HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry, render_log_solution  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
HID_KINDS = ("click", "type", "enter", "log_shortcut", "key", "move")
HELD_HEARTBEAT = 5  # the counter the stopped App keeps showing


class HeartbeatStoppedDfuKVM(SyntheticDfuKVM):
    """A DFU screen whose sequence and PTS move while the Log Solution heartbeat cells hold still."""

    def latest_frame(self):
        import time as _time

        self._frame_seq += 1
        now = _time.monotonic()
        app = render_log_solution(self.log_state, self.log_statuses or ["WAITING"] * 4, heartbeat=HELD_HEARTBEAT)
        return make_snapshot(beside(self.frame, app), sequence=self._frame_seq, received_monotonic=now,
                             stream_id="synthetic", pts=now)


class CoveredHeartbeatDfuKVM(SyntheticDfuKVM):
    """A DFU screen with a live App whose heartbeat box is covered by another window (grey, all four cells gone).

    The state marker stays readable. The heartbeat box is contract 1.3 (278, 0) 26x26 at scale 2, origin (23, 31).
    """

    def latest_frame(self):
        import time as _time

        from protocol_helpers import live_heartbeat

        self._frame_seq += 1
        now = _time.monotonic()
        app = render_log_solution(self.log_state, self.log_statuses or ["WAITING"] * 4,
                                  heartbeat=live_heartbeat(now))
        app[31:83, 579:631] = 128
        return make_snapshot(beside(self.frame, app), sequence=self._frame_seq, received_monotonic=now,
                             stream_id="synthetic", pts=now)


class DyingDuringProbeDfuKVM(HeartbeatStoppedDfuKVM):
    """Stopped heartbeat, and the video track ends on the probe's third picture read (the first two pass the window)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = 0

    def latest_frame(self):
        self.reads += 1
        snap = super().latest_frame()
        if self.reads >= 3:
            self.end_track()
        return snap


def _hid(kvm):
    return [event for event in kvm.events if event[0] in HID_KINDS]


@pytest.fixture
def serve(tmp_path):
    servers = []

    def _make(kvm):
        root = tmp_path / "template"
        save_templates(root, FOUR)
        srv = TcpJsonServer(
            device_registry=make_registry(),
            host="127.0.0.1",
            port=0,
            kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm),
            freeze_guard=FrameFreezeGuard(),
            template_root=root,
        )
        srv.start()
        time.sleep(0.05)
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, payload):
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def _assert_heartbeat_stopped(resp):
    assert resp["status"] == "error", resp
    assert resp["error"] == HEARTBEAT_STOPPED, resp
    assert resp["message"] == HEARTBEAT_STOPPED_MESSAGE


@pytest.mark.parametrize("log_state", ["standby", "monitoring"])
@pytest.mark.parametrize("command", ["input", "start_test"])
def test_stopped_heartbeat_refuses_dfu_hid_commands_before_any_hid(serve, command, log_state):
    kvm = HeartbeatStoppedDfuKVM(FOUR)
    kvm.log_state = log_state
    srv = serve(kvm)

    if command == "input":
        resp = _send(srv, build_request("input", "in-hb", "DFU-01", {"items": [{"slot": 1, "sn": "SN1"}]}))
        expected_data = {"entered": []}
    else:
        resp = _send(srv, build_request("start_test", "st-hb", "DFU-01"))
        expected_data = {"action": ""}

    _assert_heartbeat_stopped(resp)
    assert resp["data"] == expected_data
    assert _hid(kvm) == []


@pytest.mark.parametrize(("command", "log_state", "error", "expected_data"), [
    ("input", "standby", "hid_failed", {"entered": []}),  # the same unreadable reply as a screen that cannot be read
    ("start_test", "monitoring", "monitor_not_ready", {"action": ""}),
])
def test_covered_heartbeat_with_a_readable_marker_is_unreadable_and_sends_no_hid(serve, command, log_state, error,
                                                                                  expected_data):
    kvm = CoveredHeartbeatDfuKVM(FOUR)
    kvm.log_state = log_state
    srv = serve(kvm)

    if command == "input":
        resp = _send(srv, build_request("input", "in-cov", "DFU-01", {"items": [{"slot": 1, "sn": "SN1"}]}))
    else:
        resp = _send(srv, build_request("start_test", "st-cov", "DFU-01"))

    assert resp["status"] == "error", resp
    assert resp["error"] == error, resp
    assert resp["data"] == expected_data
    assert _hid(kvm) == []


def test_kvm_dying_during_the_heartbeat_probe_is_kvm_connect_failed_and_sends_no_hid(serve):
    kvm = DyingDuringProbeDfuKVM(FOUR)
    kvm.log_state = "standby"
    srv = serve(kvm)

    resp = _send(srv, build_request("start_test", "st-die", "DFU-01"))

    assert resp["status"] == "error"
    assert resp["error"] == "kvm_connect_failed", resp
    assert _hid(kvm) == []


def test_stopped_heartbeat_refuses_monitor_start_before_any_hid_and_creates_no_round_request(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.stop_heartbeat()
    srv = serve(kvm)

    started = time.monotonic()
    resp = _send(srv, build_request("monitor_start", "ms-hb", "DFU-01", {"timeout_sec": 5}))

    _assert_heartbeat_stopped(resp)
    assert resp["data"] == {"state": "", "capacity": 0}
    assert _hid(kvm) == []
    assert srv._device_round("DFU-01").start_requested is False
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("command", ["check_start", "check_finish", "inspect"])
def test_stopped_heartbeat_ends_a_long_poll_at_once(serve, command):
    kvm = LogSolutionKVM(state="monitoring")
    kvm.stop_heartbeat()
    srv = serve(kvm)
    # With no round-start request a check answers monitor_not_ready on its first reading, before the heartbeat
    # can be judged stopped. Give the round its request so the poll keeps reading until the heartbeat decides.
    srv._device_round("DFU-01").start_requested = True

    started = time.monotonic()
    resp = _send(srv, build_request(command, f"poll-{command}", "DFU-01", {"timeout_sec": 10}))

    _assert_heartbeat_stopped(resp)
    assert time.monotonic() - started < 6, "must answer at once, not after the 10 s poll budget"
    assert _hid(kvm) == []


def test_the_display_is_accepted_again_once_its_heartbeat_moves(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.stop_heartbeat()
    srv = serve(kvm)
    _assert_heartbeat_stopped(_send(srv, build_request("inspect", "in-hb-1", "DFU-01", {"timeout_sec": 5})))

    kvm.resume_heartbeat()
    resp = _send(srv, build_request("inspect", "in-hb-2", "DFU-01", {"timeout_sec": 0}))

    assert resp["status"] == "ok", resp
    assert resp["data"]["kind"] == "waiting"


def test_core_status_latches_stopped_heartbeat_until_inspect_observes_a_change(serve):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm)

    def station():
        resp = _send(srv, build_request("core_status", "core-hb", ""))
        assert resp["status"] == "ok", resp
        return next(row for row in resp["data"]["stations"] if row["device"] == "DFU-01")

    assert station()["is_heartbeat_stopped"] is False
    kvm.stop_heartbeat()
    _assert_heartbeat_stopped(_send(srv, build_request("inspect", "latch-hb", "DFU-01", {"timeout_sec": 5})))
    assert station()["is_heartbeat_stopped"] is True

    kvm.resume_heartbeat()
    # Allow a counted observation whose cells differ from the stopped picture.
    time.sleep(1.0)
    resp = _send(srv, build_request("inspect", "clear-hb", "DFU-01", {"timeout_sec": 0}))
    assert resp["status"] == "ok", resp
    assert station()["is_heartbeat_stopped"] is False
