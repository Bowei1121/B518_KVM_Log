"""Long-polling check 1.0 over the socket, driven by synthetic Log Solution screens.

Each device gets its own synthetic KVM (keyed by the registry IP), so concurrent
devices never share a screen. Lifecycle, locking, freeze abort and disconnect cleanup
are exercised through the wire.
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
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"
DEVICE_IP = "192.168.200.1"


class Rig:
    """Server plus one synthetic KVM per device IP."""

    def __init__(self, srv, kvms):
        self.srv = srv
        self.kvms = kvms
        self.host, self.port = srv.server_address

    def kvm(self, device=DEVICE):
        return self.kvms.setdefault(self.ip_of(device), LogSolutionKVM())

    def ip_of(self, device):
        return make_registry().get(device).kvm_ip


@pytest.fixture
def rig():
    kvms = {}

    def factory(host, password=""):
        return kvms.setdefault(host, LogSolutionKVM())

    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.3),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield Rig(srv, kvms)
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _open(rig):
    return socket.create_connection((rig.host, rig.port), timeout=10.0)


def _send(sock, obj):
    sock.sendall((json.dumps({"protocol_version": "1.0", **obj}) + "\n").encode("utf-8"))


def _recv(sock, timeout=10.0):
    sock.settimeout(timeout)
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("closed")
        buf.extend(chunk)
    line, _, _ = bytes(buf).partition(b"\n")
    return json.loads(line.decode("utf-8"))


def _check_once(rig, device, req_id, timeout_sec):
    with _open(rig) as sock:
        _send(sock, {"req_id": req_id, "device": device, "cmd": "check", "timeout_sec": timeout_sec})
        return _recv(sock)


def _background_check(rig, device, req_id, timeout_sec):
    out = {}

    def run():
        out["resp"] = _check_once(rig, device, req_id, timeout_sec)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def _settle(seconds=0.7):
    """Let at least one 0.5 s poll observe the state just set."""
    time.sleep(seconds)


COMPLETE = ["PASS", "FAIL", "NOTEST", "PASS"]


def test_immediate_complete_returns_taken_fast(rig):
    rig.kvm().show("monitoring")
    th, out = _background_check(rig, DEVICE, "req-fast", 30)
    _settle()
    started = time.monotonic()
    rig.kvm().show("complete", COMPLETE)
    th.join(timeout=10)

    assert out["resp"]["status"] == "ok"
    assert out["resp"]["data"]["kind"] == "taken"
    assert out["resp"]["req_id"] == "req-fast"
    assert time.monotonic() - started < 5.0


def test_delayed_complete_holds_the_connection_until_taken(rig):
    rig.kvm().show("monitoring")
    started = time.monotonic()
    th, out = _background_check(rig, DEVICE, "req-hold", 15)
    _settle(1.5)
    rig.kvm().show("complete", COMPLETE)
    th.join(timeout=15)

    resp = out["resp"]
    assert resp["data"]["kind"] == "taken"
    assert resp["elapsed_sec"] >= 1.0
    assert time.monotonic() - started < 15.0


def test_long_poll_times_out_with_last_observed_state(rig):
    rig.kvm().show("monitoring")

    resp = _check_once(rig, DEVICE, "req-timeout", 1.5)

    assert resp["status"] == "timeout"
    assert resp["error"] == "timeout"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "monitoring"
    assert resp["elapsed_sec"] >= 1.4


def test_device_busy_while_long_poll_runs_but_other_devices_do_not_block(rig):
    rig.kvm("DFU-01").show("monitoring")
    rig.kvm("DFU-02").show("monitoring")
    th, _ = _background_check(rig, "DFU-01", "req-lp", 3)
    _settle(0.3)

    busy = _check_once(rig, "DFU-01", "req-same", 0)
    other = _check_once(rig, "DFU-02", "req-other", 0)
    th.join(timeout=10)

    assert busy["status"] == "error"
    assert busy["error"] == "device_busy"
    assert other["status"] == "ok"
    assert other["data"]["kind"] == "waiting"


def test_frozen_picture_aborts_long_poll_immediately(rig):
    rig.kvm().show("monitoring")
    rig.kvm().freeze()
    started = time.monotonic()

    resp = _check_once(rig, DEVICE, "req-frozen", 10)

    assert resp["status"] == "error"
    assert resp["error"] == "frame_frozen"
    assert time.monotonic() - started < 5.0


def test_disconnect_during_long_poll_releases_the_device_lock(rig):
    rig.kvm().show("monitoring")
    sock = _open(rig)
    _send(sock, {"req_id": "req-drop", "device": DEVICE, "cmd": "check", "timeout_sec": 30})
    _settle(0.3)
    assert rig.srv.lock_manager.is_locked(DEVICE)

    sock.close()
    deadline = time.monotonic() + 5.0
    while rig.srv.lock_manager.is_locked(DEVICE) and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not rig.srv.lock_manager.is_locked(DEVICE)


def test_invalid_timeout_is_rejected_before_polling(rig):
    with _open(rig) as sock:
        _send(sock, {"req_id": "bad-timeout", "device": DEVICE, "cmd": "check", "timeout_sec": "not-a-number"})
        resp = _recv(sock)

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"


def test_parallel_long_polls_on_distinct_devices_each_get_their_own_result(rig):
    devices = ["DFU-01", "DFU-02", "DFU-03"]
    for device in devices:
        rig.kvm(device).show("monitoring")
    results = {}

    def run(device):
        th_out = _check_once(rig, device, f"req-{device}", 8)
        results[device] = th_out

    threads = [threading.Thread(target=run, args=(d,)) for d in devices]
    for t in threads:
        t.start()
    _settle()
    for device in devices:
        rig.kvm(device).show("complete", COMPLETE)
    for t in threads:
        t.join(timeout=12)

    for device in devices:
        assert results[device]["status"] == "ok"
        assert results[device]["data"]["kind"] == "taken"
        assert results[device]["device"] == device
        assert results[device]["req_id"] == f"req-{device}"


def test_long_poll_is_fenced_per_device_round(rig):
    rig.kvm().show("monitoring")
    th, out = _background_check(rig, DEVICE, "round-1", 15)
    _settle()
    rig.kvm().show("complete", COMPLETE)
    th.join(timeout=15)
    assert out["resp"]["data"]["kind"] == "taken"

    second = _check_once(rig, DEVICE, "round-1-again", 0)

    assert second["data"]["kind"] == "already_taken"
