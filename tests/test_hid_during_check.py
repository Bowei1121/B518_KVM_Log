"""Manual HID while a check long-polls (ADR-0008 amendment): passes unless Core itself is sending HID."""

import asyncio
import socket
import sys
import threading
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


class _Chan:
    def __init__(self):
        self.sent = []

    def send(self, text):
        import json

        self.sent.append(json.loads(text))


def _client():
    from jetkvm_core import JetKVMClient

    kvm = JetKVMClient("127.0.0.1")
    kvm.rpc = _Chan()
    return kvm


def test_late_manual_rpc_error_is_not_a_dock_failure_whenever_it_arrives():
    import json

    kvm = _client()
    kvm.call_manual("wheelReport", {"wheelY": 1})  # operator's id: separate id space
    kvm.call("absMouseReport", {"x": 1, "y": 1, "buttons": 0})  # Core's own
    manual_id, auto_id = kvm.rpc.sent[0]["id"], kvm.rpc.sent[1]["id"]
    assert manual_id >= 1_000_000_000 > auto_id
    for _ in range(1000):  # any amount of later manual traffic or time cannot detach the attribution
        kvm.call_manual("wheelReport", {"wheelY": 1})
    kvm._on_rpc_message(json.dumps({"id": manual_id, "error": {"message": "manual wheel failed"}}))
    assert kvm.rpc_errors == []
    kvm._on_rpc_message(json.dumps({"id": auto_id, "error": {"message": "dock failed"}}))
    assert kvm.rpc_errors == ["dock failed"]


def _manual(srv, method, params, device="DFU-01"):
    return _send(srv, "hid", device=device, method=method, params=params)


def _press_a_and_left_button(srv):
    assert _manual(srv, "keyboardReport", {"modifier": 8, "keys": [4]})["status"] == "ok"
    assert _manual(srv, "absMouseReport", {"x": 5, "y": 6, "buttons": 1})["status"] == "ok"


def _record_clicks(kvm):
    async def click(x, y):
        kvm.rpc_calls.append(("click", x, y))

    kvm.click = click


def _dock_rig(tmp_path, monkeypatch, dock):
    import monitor_flow

    srv, kvm, _ = _check_rig(tmp_path, None, monkeypatch)
    srv.visibility_lost_sec = 0.5
    _record_clicks(kvm)
    monkeypatch.setattr(monitor_flow, "bring_log_solution_forward", dock)
    return srv, kvm


def _last(kvm, method):
    return [c for c in kvm.rpc_calls if c[0] == method][-1][1]


async def _in_thread(fn):
    import asyncio

    return await asyncio.get_running_loop().run_in_executor(None, fn)


def test_dock_handover_releases_before_the_first_click(tmp_path, monkeypatch):
    from hid_session import FlowError

    srv_box, seen = {}, {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        seen["before"] = list(kvm_arg.rpc_calls)  # target search: nothing released yet
        await kvm_arg.click(9, 9)  # Core's own click
        seen["after"] = list(kvm_arg.rpc_calls)
        seen["late"] = await _in_thread(lambda: _manual(srv_box["srv"], "keyboardReport",
                                                        {"modifier": 0, "keys": []}))
        raise FlowError("hid_failed", "stop")

    srv, kvm = _dock_rig(tmp_path, monkeypatch, dock)
    srv_box["srv"] = srv
    try:
        _press_a_and_left_button(srv)
        _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        assert [c[0] for c in seen["before"]] == ["keyboardReport", "absMouseReport"]  # only the manual presses
        assert seen["after"][2:] == [("keyboardReport", {"modifier": 0, "keys": []}),
                                     ("absMouseReport", {"x": 5, "y": 6, "buttons": 0}),
                                     ("click", 9, 9)]  # released before Core's click
        assert seen["late"]["error"] == "device_busy"  # the operator's late release is dropped, harmlessly
        assert _last(kvm, "keyboardReport") == {"modifier": 0, "keys": []}
        # Releases only: the held button is not pressed again after Core's click.
        assert _last(kvm, "absMouseReport") == {"x": 5, "y": 6, "buttons": 0}
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_partial_release_dropped_during_dock_keeps_the_still_held_modifier(tmp_path, monkeypatch):
    srv_box = {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        # Operator lets go of A only (Shift stays down) while Core merely searches for the Dock.
        await _in_thread(lambda: _manual(srv_box["srv"], "keyboardReport", {"modifier": 2, "keys": []}))
        return False

    srv, kvm = _dock_rig(tmp_path, monkeypatch, dock)
    srv_box["srv"] = srv
    try:
        assert _manual(srv, "keyboardReport", {"modifier": 2, "keys": [4]})["status"] == "ok"  # Shift+A
        reply = _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        assert reply["error"] == "log_solution_not_found"
        assert _last(kvm, "keyboardReport") == {"modifier": 2, "keys": []}  # A released, Shift kept
        assert not any(c[0] == "click" for c in kvm.rpc_calls)
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


def test_move_while_keyboard_held_does_not_touch_the_keyboard(core):
    srv, kvm = core
    assert _send(srv, "hid", method="keyboardReport", params={"modifier": 8, "keys": [4]})["status"] == "ok"
    assert srv.lock_manager.try_acquire("BT-01", holder="monitor_start")
    try:
        reply = _send(srv, "hid", method="absMouseReport", params={"x": 1, "y": 1, "buttons": 0})
        assert reply["error"] == "device_busy"
    finally:
        srv.lock_manager.release("BT-01")
    sent = len(kvm.rpc_calls)
    asyncio.run(srv._release_dropped_manual("BT-01", kvm))
    assert len(kvm.rpc_calls) == sent  # pointer move, no button change: zero HID


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
    _record_clicks(kvm)
    wrapped = _HandoverKvm(kvm, lambda: srv._neutralize_manual_hid_async("BT-01", kvm))
    with pytest.raises(FlowError) as exc:
        asyncio.run(wrapped.click(0, 0))
    assert exc.value.code == "hid_failed"
    assert ("absMouseReport", {"x": 1, "y": 2, "buttons": 0}) in kvm.rpc_calls  # the mouse part was still tried
    assert ("click", 0, 0) not in kvm.rpc_calls  # Core never acted
    st = srv._slot("BT-01").manual_hid
    assert st["remote_kbd"] == (8, (4,)) and st["remote_mouse"][2] == 0  # only the failed part is kept
    broken["on"] = False
    asyncio.run(wrapped.click(0, 0))  # retried on the next attempt
    assert ("keyboardReport", {"modifier": 0, "keys": []}) in kvm.rpc_calls
    assert srv._slot("BT-01").manual_hid["remote_kbd"] == (0, ())


def test_unconfirmed_async_release_keeps_state_and_blocks_the_dock_click(monkeypatch):
    import json

    from hid_session import FlowError
    from tcp_server import TcpJsonServer, _HandoverKvm

    kvm = _client()
    srv = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0)
    st = srv._slot("BT-01").manual_hid
    st["remote_kbd"] = st["intent_kbd"] = (8, (4,))
    kvm.size = (1920, 1080)

    async def scenario(reply):
        loop = asyncio.get_running_loop()
        wrapped = _HandoverKvm(kvm, lambda: srv._neutralize_manual_hid_async("BT-01", kvm))
        task = asyncio.ensure_future(wrapped.click(10, 10))
        await asyncio.sleep(0.05)
        release = kvm.rpc.sent[-1]
        assert release["method"] == "keyboardReport" and release["params"]["keys"] == []
        if reply == "error":
            loop.call_soon(kvm._on_rpc_message, json.dumps({"id": release["id"],
                                                            "error": {"message": "USB release failed"}}))
        elif reply == "ok":
            loop.call_soon(kvm._on_rpc_message, json.dumps({"id": release["id"], "result": None}))
        await task

    sent_before = len(kvm.rpc.sent)
    with pytest.raises(FlowError):
        asyncio.run(scenario("error"))
    assert len(kvm.rpc.sent) == sent_before + 1  # only the release: no Dock mouse report went out
    assert st["remote_kbd"] == (8, (4,))  # kept for a retry

    import jetkvm_core

    monkeypatch.setattr(jetkvm_core, "CONFIRM_TIMEOUT_SEC", 0.1)
    with pytest.raises(FlowError):  # no answer at all: bounded wait, still zero clicks
        asyncio.run(scenario("none"))
    assert all(m["method"] == "keyboardReport" for m in kvm.rpc.sent)
    assert st["remote_kbd"] == (8, (4,))
    # a late error for the unanswered release is the operator-space's, not a Dock failure
    kvm._on_rpc_message(json.dumps({"id": kvm.rpc.sent[-1]["id"], "error": {"message": "late"}}))
    assert kvm.rpc_errors == []

    asyncio.run(scenario("ok"))  # confirmed: state cleared, then the click goes out
    assert st["remote_kbd"] == (0, ())
    assert kvm.rpc.sent[-1]["method"] == "absMouseReport"


def test_manual_hid_is_refused_until_the_reconcile_ack_arrives(tmp_path, monkeypatch):
    """A stale ACK must never overwrite a newer press: the lock / dock flag outlives the reconcile."""
    srv_box, seen = {}, {}

    async def dock(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        await _in_thread(lambda: _manual(srv_box["srv"], "keyboardReport", {"modifier": 0, "keys": []}))
        return False

    srv, kvm = _dock_rig(tmp_path, monkeypatch, dock)
    srv_box["srv"] = srv

    async def slow_confirmed(method, params):
        kvm.rpc_calls.append((method, params))
        seen["flag"] = srv._slot("DFU-01").dock_recovering
        seen["during"] = await _in_thread(lambda: _manual(srv, "keyboardReport", {"modifier": 2, "keys": [5]}))

    kvm.call_confirmed = slow_confirmed
    try:
        assert _manual(srv, "keyboardReport", {"modifier": 8, "keys": [4]})["status"] == "ok"
        _send(srv, "check_start", device="DFU-01", timeout_sec=3)
        assert seen["flag"] is True and seen["during"]["error"] == "device_busy"
        # The refused Shift+B is never replayed: the target ends neutral, not with B down.
        assert _last(kvm, "keyboardReport") == {"modifier": 0, "keys": []}
        assert srv._slot("DFU-01").manual_hid["remote_kbd"] == (0, ())
        assert srv._slot("DFU-01").dock_recovering is False
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_cancelled_reconcile_still_returns_the_lock_and_the_lease(core):
    from tcp_server import CommandSpec, _Admitted

    srv, kvm = core

    async def noop(ctx, args):
        return {"status": "ok", "data": {}}

    srv.register_handler(CommandSpec("noop_lock", noop, device_bound=True))
    st = srv._slot("BT-01").manual_hid
    st["remote_kbd"], st["intent_kbd"] = (2, (4,)), (0, ())  # a refused release to deliver when the command ends
    waiting = threading.Event()

    async def stuck(method, params):
        waiting.set()
        await asyncio.sleep(30)

    kvm.call_confirmed = stuck

    async def scenario():
        admitted = _Admitted(srv._commands["noop_lock"], srv.device_registry.get("BT-01"), None)
        task = asyncio.ensure_future(srv._dispatch_command("r1", admitted))
        for _ in range(300):
            if waiting.is_set():
                break
            await asyncio.sleep(0.01)
        assert waiting.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run_coroutine_threadsafe(scenario(), srv._loop).result(10)
    assert not srv.lock_manager.is_locked("BT-01")  # a later command can run
    assert all(entry.in_use == 0 for entry in srv.kvm_pool._pool.values())  # the KVM lease came back
    assert _send(srv, "hid", method="wheelReport", params={"wheelY": 1})["status"] == "ok"


def test_lock_taking_command_releases_manual_state(core):
    srv, kvm = core
    for method, params in (("keyboardReport", {"modifier": 2, "keys": [4]}),
                           ("absMouseReport", {"x": 1, "y": 2, "buttons": 1})):
        assert _send(srv, "hid", method=method, params=params)["status"] == "ok"
    kvm.rpc_calls.clear()
    asyncio.run(srv._neutralize_manual_hid_async("BT-01", kvm))
    assert kvm.rpc_calls == [("keyboardReport", {"modifier": 0, "keys": []}),
                             ("absMouseReport", {"x": 1, "y": 2, "buttons": 0})]
    kvm.rpc_calls.clear()
    asyncio.run(srv._neutralize_manual_hid_async("BT-01", kvm))
    assert kvm.rpc_calls == []  # nothing left down: nothing sent


def test_refused_new_press_is_never_replayed(core):
    srv, kvm = core
    assert srv.lock_manager.try_acquire("BT-01", holder="monitor_start")
    try:
        assert _send(srv, "hid", method="absMouseReport",
                     params={"x": 3, "y": 4, "buttons": 1})["error"] == "device_busy"
        assert _send(srv, "hid", method="keyboardReport",
                     params={"modifier": 8, "keys": [4]})["error"] == "device_busy"
    finally:
        srv.lock_manager.release("BT-01")
    asyncio.run(srv._release_dropped_manual("BT-01", kvm))
    assert kvm.rpc_calls == []  # neutral remote, refused presses, no later input: zero HID


def test_frozen_refusal_with_a_refused_press_sends_nothing(tmp_path, monkeypatch):
    srv, kvm, _ = _check_rig(tmp_path, None, monkeypatch)
    try:
        kvm.freeze()
        time.sleep(3.2)
        sent_before = len(kvm.rpc_calls)
        assert srv.lock_manager.try_acquire("DFU-01", holder="monitor_start")
        try:
            assert _manual(srv, "absMouseReport", {"x": 3, "y": 4, "buttons": 1})["error"] == "device_busy"
        finally:
            srv.lock_manager.release("DFU-01")
        reply = _send(srv, "monitor_start", device="DFU-01", timeout_sec=1)
        assert reply["status"] != "ok" and len(kvm.rpc_calls) == sent_before
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def test_release_refused_during_an_ack_wait_is_picked_up_by_a_later_pass(core):
    from tcp_server import CommandSpec, _Admitted

    srv, kvm = core

    async def noop(ctx, args):
        return {"status": "ok", "data": {}}

    srv.register_handler(CommandSpec("noop_lock", noop, device_bound=True))
    st = srv._slot("BT-01").manual_hid
    st["remote_kbd"], st["intent_kbd"] = (2, (4,)), (2, ())  # A released while refused, Shift still held
    st["remote_mouse"], st["intent_mouse"] = (5, 6, 1), (5, 6, 1)  # button still held for now
    seen = {"acks": 0}

    async def confirmed(method, params):
        kvm.rpc_calls.append((method, params))
        seen["acks"] += 1
        if seen["acks"] == 1:  # the operator lets go of the button while the first ACK is pending
            seen["busy"] = await _in_thread(lambda: _send(srv, "hid", method="absMouseReport",
                                                          params={"x": 5, "y": 6, "buttons": 0}))
            await _in_thread(lambda: _send(srv, "hid", method="absMouseReport",
                                           params={"x": 5, "y": 6, "buttons": 1}))  # and presses again: refused

    kvm.call_confirmed = confirmed

    async def scenario():
        admitted = _Admitted(srv._commands["noop_lock"], srv.device_registry.get("BT-01"), None)
        await srv._dispatch_command("r1", admitted)

    asyncio.run_coroutine_threadsafe(scenario(), srv._loop).result(10)
    assert seen["busy"]["error"] == "device_busy"
    assert kvm.rpc_calls[0] == ("keyboardReport", {"modifier": 2, "keys": []})
    assert st["remote_kbd"] == (2, ())  # Shift kept
    assert st["remote_mouse"][2] == 0  # the release refused during the ACK wait was still delivered
    assert not srv.lock_manager.is_locked("BT-01")
