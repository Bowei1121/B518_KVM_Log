"""input, start_test and monitor_start over the socket with a frozen picture (ADR-0005, 2026-10-20 第 3 點).

A frozen picture is refused as frame_frozen before any HID: the fakes record every click, key and type,
and the tests assert that list stays empty. monitor_start also stops inside its wait when the picture freezes.
"""

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import build_request, dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dfu_helpers import SyntheticDfuKVM, save_templates  # noqa: E402
from fake_kvm import make_snapshot  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
FREEZE_THRESHOLD = 0.3  # short window: a frozen reading answers in well under a second
FROZEN_PTS = 10.0
HID_KINDS = ("click", "type", "enter", "log_shortcut", "key", "move")


class StaticPtsDfuKVM(SyntheticDfuKVM):
    """A DFU screen delivered on time whose presentation timestamp never advances (a frozen HDMI stream)."""

    def latest_frame(self):
        snap = super().latest_frame()
        return make_snapshot(snap.image, sequence=snap.sequence, received_monotonic=snap.received_monotonic,
                             stream_id=snap.stream_id, pts=FROZEN_PTS)


class DyingDfuKVM(StaticPtsDfuKVM):
    """A DFU screen whose video track ends during the first picture read, and whose PTS then stays still.

    A real dead track stops delivering frames, so the window ends FROZEN; the reply must still be kvm_connect_failed.
    """

    def latest_frame(self):
        snap = super().latest_frame()
        self.end_track()
        return snap


class DyingLogSolutionKVM(LogSolutionKVM):
    """A Log Solution screen whose video track ends during the first picture read, with PTS frozen."""

    def latest_frame(self):
        snap = super().latest_frame()
        self.end_track()
        self.freeze()
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
            freeze_guard=FrameFreezeGuard(freeze_threshold=FREEZE_THRESHOLD),
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


def _input(srv):
    return _send(srv, build_request("input", "in-frz", "DFU-01", {"items": [{"slot": 1, "sn": "SN1"}]}))


def _start_test(srv):
    return _send(srv, build_request("start_test", "st-frz", "DFU-01"))


def _monitor_start(srv, timeout_sec=5):
    return _send(srv, build_request("monitor_start", "ms-frz", "DFU-01", {"timeout_sec": timeout_sec}))


@pytest.mark.parametrize(("command", "log_state"), [
    ("input", "standby"),
    ("input", "monitoring"),  # the existing Log Solution refusal would say log_solution_busy
    ("start_test", "monitoring"),
    ("start_test", "standby"),  # the existing Log Solution refusal would say monitor_not_ready
])
def test_frozen_picture_refuses_dfu_hid_commands_before_any_hid(serve, command, log_state):
    kvm = StaticPtsDfuKVM(FOUR)
    kvm.log_state = log_state
    srv = serve(kvm)

    resp = _input(srv) if command == "input" else _start_test(srv)

    assert resp["status"] == "error"
    assert resp["error"] == "frame_frozen"
    assert resp["data"] == ({"entered": []} if command == "input" else {"action": ""})
    assert _hid(kvm) == []


def test_frozen_picture_refuses_monitor_start_before_any_hid_and_creates_no_round_request(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.freeze()
    srv = serve(kvm)

    started = time.monotonic()
    resp = _monitor_start(srv)

    assert resp["status"] == "error"
    assert resp["error"] == "frame_frozen"
    assert resp["data"] == {"state": "", "capacity": 0}
    assert _hid(kvm) == []
    assert srv._device_round("DFU-01").start_requested is False
    assert time.monotonic() - started < 5


def test_missing_pts_refuses_monitor_start_as_frame_frozen(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.omit_presentation_time = True
    srv = serve(kvm)

    resp = _monitor_start(srv)

    assert resp["error"] == "frame_frozen"
    assert "Presentation Timestamp" in resp["message"]
    assert _hid(kvm) == []


@pytest.mark.parametrize("command", ["input", "start_test", "monitor_start"])
def test_kvm_dying_inside_the_window_is_kvm_connect_failed_and_sends_no_hid(serve, command):
    kvm = DyingLogSolutionKVM(state="standby") if command == "monitor_start" else DyingDfuKVM(FOUR)
    srv = serve(kvm)

    if command == "input":
        resp = _input(srv)
    elif command == "start_test":
        resp = _start_test(srv)
    else:
        resp = _monitor_start(srv)

    assert resp["status"] == "error"
    assert resp["error"] == "kvm_connect_failed"
    assert _hid(kvm) == []


def _monitor_start_waiting_on_preparing(srv, kvm, after_preparing):
    """Run monitor_start (budget 30 s) on a Preparing screen; ``after_preparing`` acts once Preparing is seen."""
    out = {}

    def run():
        out["resp"] = _monitor_start(srv, timeout_sec=30)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    # Preparing is seen exactly when the round-start request is set: act after that, while monitor_start waits.
    rnd = srv._device_round("DFU-01")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not rnd.start_requested:
        time.sleep(0.01)
    started = time.monotonic()
    after_preparing()
    thread.join(timeout=10)
    return out.get("resp"), time.monotonic() - started


def test_monitor_start_stops_at_once_when_the_picture_freezes_while_waiting(serve):
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)

    resp, elapsed = _monitor_start_waiting_on_preparing(srv, kvm, kvm.freeze)

    assert resp is not None
    assert resp["error"] == "frame_frozen", resp
    assert elapsed < 5, "must not wait for the 30 s budget"
    assert _hid(kvm) == []


def test_kvm_dying_while_monitor_start_waits_is_kvm_connect_failed_not_frame_frozen(serve):
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)

    def kill():
        kvm.freeze()
        kvm.end_track()

    resp, elapsed = _monitor_start_waiting_on_preparing(srv, kvm, kill)

    assert resp is not None
    assert resp["error"] == "kvm_connect_failed", resp
    assert elapsed < 5, "a dead KVM must be reported at once, not after the freeze threshold or the 30 s budget"
    assert _hid(kvm) == []


def test_frozen_monitor_start_keeps_the_input_release_of_a_settled_round(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.freeze()
    srv = serve(kvm)
    rnd = srv._device_round("DFU-01")
    rnd.input_released = True

    resp = _monitor_start(srv)

    assert resp["error"] == "frame_frozen"
    assert rnd.input_released is True
    assert _hid(kvm) == []
