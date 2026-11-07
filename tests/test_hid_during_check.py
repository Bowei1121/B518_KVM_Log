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


def test_late_manual_rpc_error_is_not_a_dock_failure():
    import json

    from jetkvm_core import JetKVMClient

    class Chan:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(json.loads(text))

    kvm = JetKVMClient("127.0.0.1")
    kvm.rpc = Chan()
    kvm.call_manual("wheelReport", {"wheelY": 1})  # id 1: the operator's
    kvm.call("absMouseReport", {"x": 1, "y": 1, "buttons": 0})  # id 2: Core's own
    kvm._on_rpc_message(json.dumps({"id": 1, "error": {"message": "manual wheel failed"}}))
    assert kvm.rpc_errors == []  # the late manual error is attributed to the operator
    kvm._on_rpc_message(json.dumps({"id": 2, "error": {"message": "dock failed"}}))
    assert kvm.rpc_errors == ["dock failed"]  # Core's own error still counts
    assert kvm._manual_ids == {}


def _press_a_and_left_button(srv):
    for method, params in (("keyboardReport", {"modifier": 8, "keys": [4]}),
                           ("absMouseReport", {"x": 5, "y": 6, "buttons": 1})):
        assert _send(srv, "hid", device="DFU-01", method=method, params=params)["status"] == "ok"


def _dock_rig(tmp_path, monkeypatch, dock):
    import monitor_flow

    srv, kvm, _ = _check_rig(tmp_path, None, monkeypatch)
    srv.visibility_lost_sec = 0.5
    monkeypatch.setattr(monitor_flow, "bring_log_solution_forward", dock)
    return srv, kvm


def _last(kvm, method):
    return [c for c in kvm.rpc_calls if c[0] == method][-1][1]


def test_dock_handover_releases_before_the_first_click(tmp_path, monkeypatch):
    from hid_session import FlowError

    srv_box, seen = {}, {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        import asyncio

        seen["before"] = list(kvm_arg.rpc_calls)  # target search: nothing released yet
        kvm_arg.call("absMouseReport", {"x": 9, "y": 9, "buttons": 1})  # Core's own click
        seen["after"] = list(kvm_arg.rpc_calls)
        seen["late"] = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _send(srv_box["srv"], "hid", device="DFU-01", method="keyboardReport",
                                params={"modifier": 0, "keys": []}))
        raise FlowError("hid_failed", "stop")

    srv, kvm = _dock_rig(tmp_path, monkeypatch, dock)
    srv_box["srv"] = srv
    try:
        _press_a_and_left_button(srv)
        _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        assert [c[0] for c in seen["before"]] == ["keyboardReport", "absMouseReport"]  # only the manual presses
        released = seen["after"][2:]
        assert released[:2] == [("keyboardReport", {"modifier": 0, "keys": []}),
                                ("absMouseReport", {"x": 5, "y": 6, "buttons": 0})]  # before Core's click
        assert released[2] == ("absMouseReport", {"x": 9, "y": 9, "buttons": 1})
        assert seen["late"]["error"] == "device_busy"  # the operator's late release is dropped, harmlessly
        assert _last(kvm, "keyboardReport") == {"modifier": 0, "keys": []}
        assert _last(kvm, "absMouseReport")["buttons"] == 1  # Core's click, no manual state left
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_dock_without_target_still_releases_a_dropped_manual_release(tmp_path, monkeypatch):
    srv_box = {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        import asyncio

        await asyncio.get_running_loop().run_in_executor(  # operator lets go while Core only searches
            None, lambda: [_send(srv_box["srv"], "hid", device="DFU-01", method="keyboardReport",
                                 params={"modifier": 0, "keys": []}),
                           _send(srv_box["srv"], "hid", device="DFU-01", method="absMouseReport",
                                 params={"x": 5, "y": 6, "buttons": 0})])
        return False  # Dock icon not on screen: Core never clicked

    srv, kvm = _dock_rig(tmp_path, monkeypatch, dock)
    srv_box["srv"] = srv
    try:
        _press_a_and_left_button(srv)
        reply = _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        assert reply["error"] == "log_solution_not_found"
        assert _last(kvm, "keyboardReport") == {"modifier": 0, "keys": []}
        assert _last(kvm, "absMouseReport")["buttons"] == 0
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_refusal_before_any_automated_hid_sends_no_release(tmp_path, monkeypatch):
    srv, kvm, _ = _check_rig(tmp_path, None, monkeypatch)
    try:
        _press_a_and_left_button(srv)
        kvm.freeze()
        sent_before = len(kvm.rpc_calls)
        time.sleep(3.2)  # let the frozen picture go stale
        reply = _send(srv, "monitor_start", device="DFU-01", timeout_sec=1)
        assert reply["status"] != "ok"
        assert len(kvm.rpc_calls) == sent_before  # the refusal stayed zero-HID (ADR-0005)
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_failed_release_is_kept_for_retry_and_blocks_the_automated_action(core):
    from hid_session import FlowError
    from tcp_server import _HandoverKvm

    srv, kvm = core
    for method, params in (("keyboardReport", {"modifier": 8, "keys": [4]}),
                           ("absMouseReport", {"x": 1, "y": 2, "buttons": 1})):
        assert _send(srv, "hid", method=method, params=params)["status"] == "ok"
    kvm.rpc_calls.clear()
    real = kvm.call
    broken = {"on": True}

    def call(method, params):
        if broken["on"] and method == "keyboardReport":
            raise OSError("send failed")
        real(method, params)

    kvm.call = call
    clicked = []
    kvm.click = lambda *a: clicked.append(a)
    wrapped = _HandoverKvm(kvm, lambda: srv._neutralize_manual_hid("BT-01", kvm))
    with pytest.raises(FlowError) as exc:
        wrapped.call("absMouseReport", {"x": 0, "y": 0, "buttons": 1})
    assert exc.value.code == "hid_failed"
    assert ("absMouseReport", {"x": 1, "y": 2, "buttons": 0}) in kvm.rpc_calls  # the mouse part was still tried
    assert ("absMouseReport", {"x": 0, "y": 0, "buttons": 1}) not in kvm.rpc_calls  # Core never acted
    state = srv._slot("BT-01").manual_hid
    assert state["kbd"] is not None and state["mouse"] is None  # only the failed part is kept
    broken["on"] = False
    wrapped.call("absMouseReport", {"x": 0, "y": 0, "buttons": 1})  # retried on the next attempt
    assert ("keyboardReport", {"modifier": 0, "keys": []}) in kvm.rpc_calls
    assert srv._slot("BT-01").manual_hid["kbd"] is None


def test_manual_id_table_flow_controls_instead_of_evicting():
    import json

    from jetkvm_core import MANUAL_ID_MAX, JetKVMClient

    class Chan:
        def send(self, text):
            pass

    kvm = JetKVMClient("127.0.0.1")
    kvm.rpc = Chan()
    for _ in range(MANUAL_ID_MAX):
        kvm.call_manual("wheelReport", {"wheelY": 1})
    with pytest.raises(RuntimeError):
        kvm.call_manual("wheelReport", {"wheelY": 1})  # refused, id 1 keeps its attribution
    kvm._on_rpc_message(json.dumps({"id": 1, "error": {"message": "late manual error"}}))
    assert kvm.rpc_errors == []
    kvm.call("absMouseReport", {"x": 1, "y": 1, "buttons": 0})
    kvm._on_rpc_message(json.dumps({"id": MANUAL_ID_MAX + 1, "error": {"message": "dock error"}}))
    assert kvm.rpc_errors == ["dock error"]
    kvm.call_manual("wheelReport", {"wheelY": 1})  # a freed slot accepts the next report


def test_lock_taking_command_releases_manual_state(core):
    srv, kvm = core
    for method, params in (("keyboardReport", {"modifier": 2, "keys": [4]}),
                           ("absMouseReport", {"x": 1, "y": 2, "buttons": 1})):
        assert _send(srv, "hid", method=method, params=params)["status"] == "ok"
    kvm.rpc_calls.clear()
    srv._neutralize_manual_hid("BT-01", kvm)
    assert kvm.rpc_calls == [("keyboardReport", {"modifier": 0, "keys": []}),
                             ("absMouseReport", {"x": 1, "y": 2, "buttons": 0})]
    kvm.rpc_calls.clear()
    srv._neutralize_manual_hid("BT-01", kvm)
    assert kvm.rpc_calls == []  # nothing left down: nothing sent
