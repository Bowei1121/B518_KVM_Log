"""Manual HID `hid` command (ADR-0008): success, validation, dropped while a locked command holds the device."""

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_channel import HidSender  # noqa: E402
from core_protocol import dumps_request  # noqa: E402
from fake_kvm import FakeKVM  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry, recv_reply, wire  # noqa: E402
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


def _hid(srv, method, params, device="BT-01", **extra):
    with socket.create_connection(srv.server_address, timeout=5.0) as sock:
        sock.sendall(dumps_request(wire({"cmd": "hid", "device": device, "method": method,
                                         "params": params, **extra})))
        return recv_reply(sock)


def test_hid_forwards_whitelisted_reports(core):
    srv, kvm = core
    assert _hid(srv, "absMouseReport", {"x": 100, "y": 200, "buttons": 1})["status"] == "ok"
    assert _hid(srv, "keyboardReport", {"modifier": 2, "keys": [4]})["status"] == "ok"
    assert _hid(srv, "wheelReport", {"wheelY": -1})["status"] == "ok"
    assert kvm.rpc_calls == [("absMouseReport", {"x": 100, "y": 200, "buttons": 1}),
                             ("keyboardReport", {"modifier": 2, "keys": [4]}),
                             ("wheelReport", {"wheelY": -1})]


@pytest.mark.parametrize("method,params", [
    ("rebootDevice", {}),
    ("absMouseReport", {"x": 1, "y": 2}),
    ("absMouseReport", {"x": 40000, "y": 2, "buttons": 0}),
    ("absMouseReport", {"x": True, "y": 2, "buttons": 0}),
    ("absMouseReport", {"x": 1, "y": 2, "buttons": 0, "evil": 1}),
    ("keyboardReport", {"modifier": 0, "keys": [1, 2, 3, 4, 5, 6, 7]}),
    ("keyboardReport", {"modifier": 0, "keys": ["a"]}),
    ("wheelReport", {"wheelY": 500}),
    ("keyboardReport", "nope"),
])
def test_hid_rejects_invalid_arguments(core, method, params):
    srv, kvm = core
    resp = _hid(srv, method, params)
    assert resp["status"] == "error" and resp["error"] == "invalid_argument"
    assert kvm.rpc_calls == []


def test_hid_requires_device(core):
    srv, _ = core
    assert _hid(srv, "wheelReport", {"wheelY": 1}, device="")["error"] == "missing_device"


def test_hid_dropped_while_device_lock_held(core):
    srv, kvm = core
    assert srv.lock_manager.try_acquire("BT-01")  # a LabVIEW command holds the device
    try:
        resp = _hid(srv, "absMouseReport", {"x": 1, "y": 1, "buttons": 0})
        assert resp["error"] == "device_busy"
        assert kvm.rpc_calls == []
        # another device is unaffected
        assert _hid(srv, "wheelReport", {"wheelY": 1}, device="BT-02")["status"] == "ok"
    finally:
        srv.lock_manager.release("BT-01")
    assert _hid(srv, "wheelReport", {"wheelY": 1})["status"] == "ok"


def test_hid_does_not_lock_out_labview(core):
    srv, _ = core
    _hid(srv, "wheelReport", {"wheelY": 1})
    assert not srv.lock_manager.is_locked("BT-01")


def test_hid_sender_coalesces_moves_and_drops_on_busy():
    sent = []
    gate = __import__("threading").Event()

    def send(cmd, payload, timeout, device):
        gate.wait(2)
        sent.append((device, payload["method"], payload["params"]))
        return {"status": "ok"}

    sender = HidSender(send, lambda: "BT-01")
    sender("keyboardReport", {"modifier": 0, "keys": [4]})  # worker takes this and blocks in send
    time.sleep(0.1)
    for i in range(50):
        sender("absMouseReport", {"x": i, "y": i, "buttons": 0})
    sender("absMouseReport", {"x": 9, "y": 9, "buttons": 1})
    gate.set()
    time.sleep(0.3)
    sender.close()
    moves = [s for s in sent if s[1] == "absMouseReport"]
    assert moves == [("BT-01", "absMouseReport", {"x": 49, "y": 49, "buttons": 0}),
                     ("BT-01", "absMouseReport", {"x": 9, "y": 9, "buttons": 1})]


def test_hid_sender_busy_clears_queue():
    sent = []

    def send(cmd, payload, timeout, device):
        sent.append(payload["method"])
        time.sleep(0.1)
        return {"status": "error", "error": "device_busy"}

    sender = HidSender(send, lambda: "BT-01")
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.03)
    sender("keyboardReport", {"modifier": 0, "keys": []})
    sender("wheelReport", {"wheelY": 1})
    time.sleep(0.4)
    sender.close()
    assert sent == ["keyboardReport"]


@pytest.mark.parametrize("method", [[], {}, None, 5, True])
def test_hid_non_string_method_is_invalid_argument(core, method):
    srv, kvm = core
    resp = _hid(srv, method, {"wheelY": 1})
    assert resp["status"] == "error" and resp["error"] == "invalid_argument"
    assert kvm.rpc_calls == []
    assert _hid(srv, "wheelReport", {"wheelY": 1})["status"] == "ok"


def _blocking_sender(device):
    import threading
    gate = threading.Event()
    sent = []

    def send(cmd, payload, timeout, dev):
        gate.wait(3)
        sent.append((dev, payload["method"], payload["params"]))
        return {"status": "ok"}

    return HidSender(send, lambda: device["v"]), gate, sent


def test_hid_sender_pins_device_at_enqueue():
    device = {"v": "BT-01"}
    sender, gate, sent = _blocking_sender(device)
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.1)
    sender("keyboardReport", {"modifier": 0, "keys": []})
    device["v"] = "BT-02"
    gate.set()
    time.sleep(0.3)
    sender.close()
    assert [d for d, _, _ in sent] == ["BT-01", "BT-01"]


def test_hid_sender_close_still_sends_queued_release():
    device = {"v": "BT-01"}
    sender, gate, sent = _blocking_sender(device)
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.1)
    sender("keyboardReport", {"modifier": 0, "keys": []})
    sender.close()
    gate.set()
    time.sleep(0.3)
    assert [p["keys"] for _, _, p in sent] == [[4], []]


def test_hid_sender_full_queue_never_drops_release():
    device = {"v": "BT-01"}
    sender, gate, sent = _blocking_sender(device)
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.1)
    for _ in range(HidSender.MAX_QUEUE + 50):
        sender("wheelReport", {"wheelY": 1})
    sender("keyboardReport", {"modifier": 0, "keys": []})
    sender("absMouseReport", {"x": 1, "y": 1, "buttons": 1})
    sender("absMouseReport", {"x": 1, "y": 1, "buttons": 0})
    sender.close()
    gate.set()
    time.sleep(0.6)
    kb = [p["keys"] for _, m, p in sent if m == "keyboardReport"]
    assert kb[-1] == []
    mouse = [p["buttons"] for _, m, p in sent if m == "absMouseReport"]
    assert mouse[-1] == 0  # close keeps only the final state, which is released


def test_hid_sender_join_waits_for_queued_release():
    device = {"v": "BT-01"}
    sender, gate, sent = _blocking_sender(device)
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.1)
    sender("keyboardReport", {"modifier": 0, "keys": []})
    sender.close()
    assert sender.join(0.1) is False  # still blocked in send
    gate.set()
    assert sender.join(2.0) is True
    assert [p["keys"] for _, _, p in sent] == [[4], []]


def test_hid_sender_close_collapses_backlog_to_final_state():
    device = {"v": "BT-01"}
    sender, gate, sent = _blocking_sender(device)
    sender("keyboardReport", {"modifier": 0, "keys": [4]})
    time.sleep(0.1)
    for key in ([5], [6], []):
        sender("keyboardReport", {"modifier": 0, "keys": key})
    for _ in range(20):
        sender("wheelReport", {"wheelY": 1})
    sender.close()
    assert not sender.is_done()  # still blocked in the in-flight send: the UI must keep waiting
    gate.set()
    assert sender.join(2.0) and sender.is_done()
    assert [p["keys"] for _, m, p in sent if m == "keyboardReport"] == [[4], []]
    assert not [1 for _, m, _ in sent if m == "wheelReport"]
