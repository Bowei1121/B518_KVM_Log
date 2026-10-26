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


def _assert_not_frozen_in_status(srv):
    status = _send(srv, build_request("status", "status-after-loss", ""))
    assert status["status"] == "ok"
    device = next(row for row in status["data"]["devices"] if row["device"] == "DFU-01")
    assert device["frozen"] is False
    core = _send(srv, build_request("core_status", "core-after-loss", ""))
    assert core["status"] == "ok"
    assert "DFU-01" not in core["data"]["freeze_guard"]["frozen_devices"]


@pytest.mark.parametrize("command", ["input", "start_test", "monitor_start"])
def test_kvm_lost_inside_window_does_not_latch_status_frozen(serve, command):
    kvm = DyingLogSolutionKVM(state="standby") if command == "monitor_start" else DyingDfuKVM(FOUR)
    srv = serve(kvm)
    send = {"input": _input, "start_test": _start_test, "monitor_start": _monitor_start}[command]
    assert send(srv)["error"] == "kvm_connect_failed"
    _assert_not_frozen_in_status(srv)


@pytest.mark.parametrize("command", ["input", "start_test"])
def test_no_frame_kvm_lost_during_heartbeat_probe_releases_latch(serve, monkeypatch, command):
    kvm = StaticPtsDfuKVM(FOUR)
    monkeypatch.setattr(kvm, "latest_frame", lambda: None)
    srv = serve(kvm)

    sample_heartbeat = srv._heartbeat_refusal
    probe_reads = []

    def dying_probe_read():
        probe_reads.append(1)
        kvm.end_track()  # the KVM drops while the heartbeat probe is sampling
        return None

    async def die_during_probe(ctx):
        # wait_live has already latched FROZEN(no_frame) with the KVM still alive.
        assert ctx.kvm.is_healthy()
        assert srv.freeze_guard.stream("DFU-01").last_verdict().reason == "no_frame"
        assert "DFU-01" in srv.freeze_guard.get_status()["frozen_devices"]
        monkeypatch.setattr(kvm, "latest_frame", dying_probe_read)
        return await sample_heartbeat(ctx)

    monkeypatch.setattr(srv, "_heartbeat_refusal", die_during_probe)
    send = _input if command == "input" else _start_test
    assert send(srv)["error"] == "kvm_connect_failed"
    assert probe_reads  # the track ended inside the real heartbeat sampling
    assert "DFU-01" not in srv.freeze_guard.get_status()["frozen_devices"]
    _assert_not_frozen_in_status(srv)


def test_kvm_lost_during_monitor_wait_read_does_not_latch_status_frozen(serve):
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)

    def die_on_next_read():
        read = kvm.latest_frame
        kvm.freeze()

        def dying_read():
            time.sleep(FREEZE_THRESHOLD)
            kvm.end_track()
            return read()

        kvm.latest_frame = dying_read

    resp, _ = _monitor_start_waiting_on_preparing(srv, kvm, die_on_next_read)
    assert resp is not None
    assert resp["error"] == "kvm_connect_failed"
    _assert_not_frozen_in_status(srv)


@pytest.mark.parametrize("command", ["inspect", "check_finish"])
@pytest.mark.parametrize("before_loss, after_loss", [
    (0.3, 0.3),  # 0.6 s read: done before the next poll, which replies kvm_connect_failed
    (0.1, 1.0),  # read still running when kvm_connect_failed is replied; it releases its own latch
])
def test_slow_read_finishing_after_timeout_and_kvm_loss_releases_own_latch(serve, command, before_loss, after_loss):
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)
    srv.inspection_timeout_sec = 0.2
    read = kvm.latest_frame

    def slow_dying_read():
        time.sleep(before_loss)
        kvm.end_track()
        time.sleep(after_loss)
        return read()

    kvm.omit_presentation_time = True
    kvm.latest_frame = slow_dying_read
    resp = _send(srv, build_request(command, "slow-loss", "DFU-01", {"timeout_sec": 5}))
    assert resp["error"] == "kvm_connect_failed"
    time.sleep(before_loss + after_loss + 0.5)
    assert "DFU-01" not in srv.freeze_guard.get_status()["frozen_devices"]
    _assert_not_frozen_in_status(srv)


def test_kvm_loss_reply_keeps_server_latch_from_before_disconnect(serve):
    kvm = LogSolutionKVM(state="monitoring")
    kvm.omit_presentation_time = True
    srv = serve(kvm)
    srv.inspection_timeout_sec = 5
    assert _send(srv, build_request("inspect", "latch-first", "DFU-01", {"timeout_sec": 5}))["error"] == "frame_frozen"
    read = kvm.latest_frame

    def dying_read():
        kvm.end_track()
        return read()

    kvm.latest_frame = dying_read
    assert _send(srv, build_request("inspect", "loss-next", "DFU-01", {"timeout_sec": 5}))["error"] == "kvm_connect_failed"
    # 僅驗證既有 latch 保留；斷線後 status 顯示方式仍待使用者決定。
    assert "DFU-01" in srv.freeze_guard.get_status()["frozen_devices"]


def test_frozen_before_kvm_loss_stays_frozen_while_disconnected(serve):
    """User decision 2026-10-26: a device frozen before its KVM drops keeps showing frozen while disconnected."""
    kvm = LogSolutionKVM(state="monitoring")
    kvm.omit_presentation_time = True
    srv = serve(kvm)
    srv.inspection_timeout_sec = 5
    assert _send(srv, build_request("inspect", "frozen-first", "DFU-01", {"timeout_sec": 5}))["error"] == "frame_frozen"
    read = kvm.latest_frame

    def dying_read():
        kvm.end_track()
        return read()

    kvm.latest_frame = dying_read
    assert _send(srv, build_request("inspect", "then-loss", "DFU-01", {"timeout_sec": 5}))["error"] == "kvm_connect_failed"

    status = _send(srv, build_request("status", "status-frozen-offline", ""))
    device = next(row for row in status["data"]["devices"] if row["device"] == "DFU-01")
    assert device["frozen"] is True
    assert device["connected"] is False
    core = _send(srv, build_request("core_status", "core-frozen-offline", ""))
    station = next(row for row in core["data"]["stations"] if row["device"] == "DFU-01")
    assert station["status"] == "FROZEN"
    assert station["is_frozen"] is True
    assert station["is_connected"] is False
    assert "DFU-01" in core["data"]["freeze_guard"]["frozen_devices"]


@pytest.mark.parametrize("command", ["inspect", "check_finish"])
def test_finished_read_verdict_releases_own_latch_before_loss_reply(command):
    """讀取完成後才斷線且另一 lane 已判定時，回覆仍用該 lane 的 verdict。"""
    import asyncio
    from concurrent.futures import Future
    from device_slot import DeviceSlot
    from round_check import CheckMode, Reading
    from tcp_server import CommandContext

    kvm = LogSolutionKVM(state="monitoring")
    guard = FrameFreezeGuard()
    slot = DeviceSlot("DFU-01", guard.stream("DFU-01"))
    verdict = slot.stream.observe("s", 1, None)
    slot.stream.observe("s", 2, None)
    future = Future()
    future.set_result(Reading.failed("frame_frozen", verdict.message, verdict=verdict))
    lane = "inspect" if command == "inspect" else "check"
    kvm.connected = True
    srv = TcpJsonServer(device_registry=make_registry(), freeze_guard=guard)

    async def completed_after_timeout(slot, kvm, mode, submitted=None):
        slot.flights[lane] = future
        if submitted is not None:
            submitted.append(future)
        kvm.end_track()
        return Reading.unknown("inspection_timeout")

    srv._read_reading = completed_after_timeout
    srv._is_running = True
    ctx = CommandContext(entry=make_registry().get("DFU-01"), kvm=kvm, slot=slot)
    try:
        mode = CheckMode.INSPECT if command == "inspect" else CheckMode.CHECK_FINISH
        reply = asyncio.run(srv._long_poll(ctx, 5, mode))
        assert reply["error"] == "kvm_connect_failed"
        assert guard.get_status()["frozen_devices"] == []
    finally:
        srv._inspector.shutdown(wait=True)


def test_loss_before_new_command_does_not_release_previous_command_verdict():
    import asyncio
    from concurrent.futures import Future
    from device_slot import DeviceSlot
    from round_check import CheckMode, Reading
    from tcp_server import CommandContext

    guard = FrameFreezeGuard()
    slot = DeviceSlot("DFU-01", guard.stream("DFU-01"))
    verdict = slot.stream.observe("s", 1, None)
    previous = Future()
    previous.set_result(Reading.failed("frame_frozen", verdict.message, verdict=verdict))
    slot.flights["inspect"] = previous
    srv = TcpJsonServer(device_registry=make_registry(), freeze_guard=guard)
    srv._is_running = True
    ctx = CommandContext(entry=make_registry().get("DFU-01"),
                         kvm=LogSolutionKVM(state="monitoring"), slot=slot)
    try:
        reply = asyncio.run(srv._long_poll(ctx, 5, CheckMode.INSPECT))
        assert reply["error"] == "kvm_connect_failed"
        # 先前命令在 KVM 存活時鎖定；斷線後 status 顯示方式仍待使用者決定。
        assert guard.get_status()["frozen_devices"] == ["DFU-01"]
    finally:
        srv._inspector.shutdown(wait=True)


def test_loss_does_not_release_another_inspects_read_submitted_during_command():
    import asyncio
    from concurrent.futures import Future
    from device_slot import DeviceSlot
    from round_check import CheckMode, Reading
    from tcp_server import CommandContext

    guard = FrameFreezeGuard()
    slot = DeviceSlot("DFU-01", guard.stream("DFU-01"))
    kvm = LogSolutionKVM(state="monitoring")
    kvm.connected = True
    srv = TcpJsonServer(device_registry=make_registry(), freeze_guard=guard)
    srv._is_running = True

    async def another_inspects_read(slot, kvm, mode, submitted=None):
        verdict = slot.stream.observe("s", 1, None)
        future = Future()
        future.set_result(Reading.failed("frame_frozen", verdict.message, verdict=verdict))
        slot.flights["inspect"] = future
        # Another inspect submitted this future; this command owns no read.
        kvm.end_track()
        return Reading.unknown("inspection_timeout")

    srv._read_reading = another_inspects_read
    ctx = CommandContext(entry=make_registry().get("DFU-01"), kvm=kvm, slot=slot)
    try:
        reply = asyncio.run(srv._long_poll(ctx, 5, CheckMode.INSPECT))
        assert reply["error"] == "kvm_connect_failed"
        assert guard.get_status()["frozen_devices"] == ["DFU-01"]
    finally:
        srv._inspector.shutdown(wait=True)
