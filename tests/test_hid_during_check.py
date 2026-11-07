"""Manual HID while a check long-polls (ADR-0008 amendment): passes unless Core itself is sending HID."""

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_channel import HidSender  # noqa: E402
from core_protocol import dumps_request  # noqa: E402
from device_lock import DeviceLockManager  # noqa: E402
from fake_kvm import FakeKVM  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry, recv_reply, wire  # noqa: E402
from stream_view import BusyNotice  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402


@pytest.fixture
def core():
    kvm = FakeKVM()
    srv = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0,
                        kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm))
    srv.start()
    time.sleep(0.05)
    yield srv, kvm
    srv.stop()
    srv.wait(timeout=2.0)


def _send(srv, cmd, device="BT-01", **extra):
    with socket.create_connection(srv.server_address, timeout=5.0) as sock:
        sock.sendall(dumps_request(wire({"cmd": cmd, "device": device, **extra})))
        return recv_reply(sock)


def _hid(srv):
    return _send(srv, "hid", method="wheelReport", params={"wheelY": 1})


def test_lock_manager_records_and_clears_holder():
    mgr = DeviceLockManager()
    assert mgr.holder("D") is None
    assert mgr.try_acquire("D", holder="check_finish")
    assert mgr.holder("D") == "check_finish"
    mgr.release("D")
    assert mgr.holder("D") is None
    assert mgr.try_acquire("D")  # legacy caller without a holder
    assert mgr.holder("D") is None and mgr.is_locked("D")


@pytest.mark.parametrize("holder", ["check_start", "check_finish"])
def test_hid_passes_while_check_waits(core, holder):
    srv, kvm = core
    assert srv.lock_manager.try_acquire("BT-01", holder=holder)
    try:
        assert _hid(srv)["status"] == "ok"
        assert kvm.rpc_calls == [("wheelReport", {"wheelY": 1})]
    finally:
        srv.lock_manager.release("BT-01")


def test_hid_dropped_while_check_docks(core):
    srv, kvm = core
    slot = srv._slot("BT-01")
    assert srv.lock_manager.try_acquire("BT-01", holder="check_finish")
    slot.dock_recovering = True
    try:
        assert _hid(srv)["error"] == "device_busy"
        assert kvm.rpc_calls == []
        slot.dock_recovering = False
        assert _hid(srv)["status"] == "ok"
    finally:
        srv.lock_manager.release("BT-01")


@pytest.mark.parametrize("holder", ["start_test", "input", "monitor_start", None])
def test_hid_dropped_for_other_holders(core, holder):
    srv, kvm = core
    assert srv.lock_manager.try_acquire("BT-01", holder=holder)
    try:
        assert _hid(srv)["error"] == "device_busy"
        assert kvm.rpc_calls == []
    finally:
        srv.lock_manager.release("BT-01")


def test_hid_during_check_does_not_lock_out_labview(core):
    srv, _ = core
    assert srv.lock_manager.try_acquire("BT-01", holder="check_finish")
    try:
        _hid(srv)
        assert srv.lock_manager.holder("BT-01") == "check_finish"  # hid never took or replaced the lock
    finally:
        srv.lock_manager.release("BT-01")
    _hid(srv)
    assert not srv.lock_manager.is_locked("BT-01")


def test_execute_passes_command_name_as_holder(core):
    srv, _ = core
    seen = []
    orig = srv.lock_manager.try_acquire

    def spy(device, holder=None):
        seen.append(holder)
        return orig(device, holder=holder)

    srv.lock_manager.try_acquire = spy
    _send(srv, "check_start", timeout_sec=0.2)
    assert seen == ["check_start"]


def _check_rig(tmp_path, dock_spy, monkeypatch):
    import threading

    import monitor_flow
    from freeze_guard import FrameFreezeGuard
    from protocol_helpers import LogSolutionKVM

    registry = make_registry()
    kvm = LogSolutionKVM(state="monitoring", statuses=["TESTING", "PASS"])
    kvm.cover()
    srv = TcpJsonServer(device_registry=registry, host="127.0.0.1", port=0,
                        kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm),
                        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0), template_root=tmp_path,
                        visibility_lost_sec=0.2, dock_recheck_sec=0.1)
    if dock_spy is not None:
        monkeypatch.setattr(monitor_flow, "bring_log_solution_forward", dock_spy)
    srv.start()
    time.sleep(0.05)
    return srv, kvm, threading


def test_hid_passes_during_real_check_poll_then_drops_while_docking(tmp_path, monkeypatch):
    from hid_session import FlowError

    srv_box = {}
    during = {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        slot = srv_box["srv"]._slot("DFU-01")
        during["flag"] = slot.dock_recovering
        import asyncio

        # The server loop is busy awaiting this coroutine: send the HID from a worker thread.
        during["reply"] = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _send(srv_box["srv"], "hid", device="DFU-01", method="wheelReport",
                                params={"wheelY": 1}))
        raise FlowError("hid_failed", "dock click failed")

    srv, kvm, _ = _check_rig(tmp_path, dock, monkeypatch)
    srv_box["srv"] = srv
    try:
        reply = _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        slot = srv._slot("DFU-01")
        assert during["flag"] is True
        assert during["reply"]["error"] == "device_busy"  # check was clicking the Dock
        assert slot.dock_recovering is False  # cleared although bring_log_solution_forward raised
        assert reply["status"] == "error" and reply["error"] == "hid_failed"
        assert ("wheelReport", {"wheelY": 1}) not in kvm.rpc_calls
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_hid_ok_and_reaches_kvm_while_check_finish_long_polls(tmp_path, monkeypatch):
    import threading

    srv, kvm, _ = _check_rig(tmp_path, None, monkeypatch)
    srv.visibility_lost_sec = 60.0  # the check only waits; it never clicks the Dock
    out = {}
    t = threading.Thread(target=lambda: out.update(
        _send(srv, "check_finish", device="DFU-01", timeout_sec=1.0)))
    try:
        t.start()
        deadline = time.monotonic() + 2
        while srv.lock_manager.holder("DFU-01") != "check_finish" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert srv.lock_manager.holder("DFU-01") == "check_finish"
        reply = _send(srv, "hid", device="DFU-01", method="wheelReport", params={"wheelY": 2})
        assert reply["status"] == "ok"
        assert ("wheelReport", {"wheelY": 2}) in kvm.rpc_calls
        assert srv.lock_manager.holder("DFU-01") == "check_finish"
        t.join(5)
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_busy_notice_throttles_redraws():
    n = BusyNotice(hold=3.0)
    assert n.update(None, 0.0) is False
    assert n.update(10.0, 10.1) is True    # first busy -> redraw once
    for i in range(100):                   # a burst of dropped moves: no redraw
        assert n.update(10.0 + i * 0.01, 10.2 + i * 0.01) is False
    assert n.visible
    assert n.update(10.99, 14.5) is True   # expired -> redraw once
    assert n.update(10.99, 15.0) is False


def test_hid_sender_records_last_busy():
    def send(cmd, payload, timeout, device):
        return {"status": "error", "error": "device_busy"}

    sender = HidSender(send, lambda: "BT-01")
    assert sender.last_busy is None
    sender("wheelReport", {"wheelY": 1})
    time.sleep(0.2)
    sender.close()
    assert sender.last_busy is not None
