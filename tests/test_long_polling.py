"""Long-polling check 1.0 over the socket, driven by synthetic Log Solution screens.

Each device gets its own synthetic KVM (keyed by the registry IP), so concurrent
devices never share a screen. Lifecycle, locking, freeze abort and disconnect cleanup
are exercised through the wire.
"""

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

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
    sock.sendall(dumps_request({"protocol_version": "1.0", **obj}))


def _recv(sock, timeout=10.0):
    sock.settimeout(timeout)
    return recv_reply(sock)


def _arm(rig, device=DEVICE, statuses=None):
    from protocol_helpers import arm_round
    return arm_round(rig.srv, rig.kvm(device), device, statuses)


def _check_once(rig, device, req_id, timeout_sec):
    with _open(rig) as sock:
        _send(sock, {"req_id": req_id, "device": device, "cmd": "check_finish", "timeout_sec": timeout_sec})
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
    _arm(rig)
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
    _arm(rig)
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
    _arm(rig)

    resp = _check_once(rig, DEVICE, "req-timeout", 1.5)

    assert resp["status"] == "timeout"
    assert resp["error"] == "timeout"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "monitoring"
    assert resp["elapsed_sec"] >= 1.4


def test_device_busy_while_long_poll_runs_but_other_devices_do_not_block(rig):
    _arm(rig, "DFU-01")
    _arm(rig, "DFU-02")
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


@pytest.mark.parametrize("death", ["end_track", "peer_failed"])
def test_kvm_death_mid_command_returns_kvm_connect_failed_without_freeze_wait(rig, death):
    """The picture keeps moving, so only the connection's own death signal can end the poll early."""
    _arm(rig)
    th, out = _background_check(rig, DEVICE, "req-dead", 30)
    _settle(0.3)
    started = time.monotonic()
    if death == "end_track":
        rig.kvm().end_track()
    else:
        rig.kvm().set_peer_state("failed")
    th.join(timeout=10)

    assert out["resp"]["status"] == "error"
    assert out["resp"]["error"] == "kvm_connect_failed"
    assert time.monotonic() - started < 5.0

    # The dead connection was evicted on release; the next command reconnects instead of failing.
    again = _check_once(rig, DEVICE, "req-again", 0)
    assert again["error"] != "kvm_connect_failed"


def test_kvm_death_inside_freeze_window_is_connection_loss_not_frozen(rig):
    """The KVM dies while Freeze Guard is still waiting: that is kvm_connect_failed, not frame_frozen."""
    rig.kvm().show("monitoring")
    rig.kvm().freeze()
    th, out = _background_check(rig, DEVICE, "req-freeze-dead", 10)
    _settle(0.15)  # inside the 0.3 s freeze window
    rig.kvm().end_track()
    th.join(timeout=10)

    assert out["resp"]["error"] == "kvm_connect_failed"


def test_disconnect_during_long_poll_releases_the_device_lock(rig):
    _arm(rig)
    sock = _open(rig)
    _send(sock, {"req_id": "req-drop", "device": DEVICE, "cmd": "check_finish", "timeout_sec": 30})
    _settle(0.3)
    assert rig.srv.lock_manager.is_locked(DEVICE)

    sock.close()
    deadline = time.monotonic() + 5.0
    while rig.srv.lock_manager.is_locked(DEVICE) and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not rig.srv.lock_manager.is_locked(DEVICE)


def test_invalid_timeout_is_rejected_before_polling(rig):
    with _open(rig) as sock:
        _send(sock, {"req_id": "bad-timeout", "device": DEVICE, "cmd": "check_finish", "timeout_sec": "not-a-number"})
        resp = _recv(sock)

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"


def test_parallel_long_polls_on_distinct_devices_each_get_their_own_result(rig):
    devices = ["DFU-01", "DFU-02", "DFU-03"]
    for device in devices:
        _arm(rig, device)
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
    _arm(rig)
    th, out = _background_check(rig, DEVICE, "round-1", 15)
    _settle()
    rig.kvm().show("complete", COMPLETE)
    th.join(timeout=15)
    assert out["resp"]["data"]["kind"] == "taken"

    second = _check_once(rig, DEVICE, "round-1-again", 0)

    assert second["data"]["kind"] == "already_taken"


class _GoneWriter:
    """Writer of a client that has already disconnected; records anything the server writes."""

    _transport = None

    def __init__(self):
        self.written = []

    def get_extra_info(self, name):
        return None

    def is_closing(self):
        return True

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


def test_cancelled_check_of_a_gone_client_writes_no_reply_and_releases_the_lock():
    kvm = LogSolutionKVM()
    kvm.show("monitoring")
    srv = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0,
                        kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm))
    srv._is_running = True
    writer = _GoneWriter()
    line = dumps_request({"protocol_version": "1.0", "req_id": "gone-1", "device": DEVICE, "cmd": "check_finish",
                          "timeout_sec": 30}).decode("utf-8")

    async def serve_one_line():
        reader = asyncio.StreamReader()
        reader.feed_data(line.encode("utf-8"))
        reader.feed_eof()
        await srv._client_connected_cb(reader, writer)

    asyncio.run(serve_one_line())

    assert writer.written == []
    assert not srv.lock_manager.is_locked(DEVICE)


def test_check_in_progress_is_answered_server_shutting_down_when_core_stops(rig):
    _arm(rig)
    sock = _open(rig)
    try:
        _send(sock, {"req_id": "req-stop", "device": DEVICE, "cmd": "check_finish", "timeout_sec": 30})
        deadline = time.monotonic() + 5.0
        while not rig.srv.lock_manager.is_locked(DEVICE) and time.monotonic() < deadline:
            time.sleep(0.05)

        rig.srv._is_running = False  # the flag stop() clears; the poll checks it on its next pass
        resp = _recv(sock)
    finally:
        sock.close()
        rig.srv._is_running = True  # so the fixture's stop() still reaches the server loop

    assert resp["req_id"] == "req-stop"
    assert resp["status"] == "error"
    assert resp["error"] == "server_shutting_down"


def test_kvm_lost_during_check_read_does_not_latch_status_frozen(rig):
    kvm = rig.kvm()
    kvm.show("monitoring")
    kvm.freeze()
    stream = rig.srv.freeze_guard.stream(DEVICE)
    stream.observe_snapshot(kvm.latest_frame())
    time.sleep(rig.srv.freeze_guard.freeze_threshold)  # the next reading of the still PTS is FROZEN
    read = kvm.latest_frame

    def dying_read():
        kvm.end_track()
        return read()

    kvm.latest_frame = dying_read
    resp = _check_once(rig, DEVICE, "req-loss-status", 10)
    assert resp["error"] == "kvm_connect_failed"
    assert DEVICE not in rig.srv.freeze_guard.get_status()["frozen_devices"]
