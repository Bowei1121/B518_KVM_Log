"""Integration and unit tests for Per-Device Lock & Fail-Fast Busy Response (Ticket 02)."""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_lock import DeviceBusyError, DeviceLockManager
from tcp_server import TcpJsonServer

_SOCK_BUFFERS: dict[int, bytearray] = {}


def send_json(sock: socket.socket, obj: dict) -> None:
    """Send a dictionary as a newline-delimited JSON string."""
    line = json.dumps(obj) + "\n"
    sock.sendall(line.encode("utf-8"))


def recv_json(sock: socket.socket, timeout: float = 3.0) -> dict:
    """Receive a newline-delimited JSON line and parse it."""
    sock.settimeout(timeout)
    buf = _SOCK_BUFFERS.setdefault(id(sock), bytearray())
    while b"\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            _SOCK_BUFFERS.pop(id(sock), None)
            raise ConnectionError("Socket closed while waiting for newline")
        buf.extend(chunk)
    nl_idx = buf.index(b"\n")
    line = bytes(buf[:nl_idx])
    del buf[:nl_idx + 1]
    return json.loads(line.decode("utf-8"))


@pytest.fixture
def running_server():
    """Fixture that starts a TcpJsonServer on an ephemeral port in a background thread."""
    server = TcpJsonServer(host="127.0.0.1", port=0)
    server.start()
    time.sleep(0.05)
    try:
        yield server
    finally:
        server.stop()


# ==============================================================================
# Unit Tests for DeviceLockManager
# ==============================================================================


def test_device_lock_manager_acquire_and_release():
    mgr = DeviceLockManager()
    assert not mgr.is_locked("1")
    assert mgr.get_active_devices() == []

    assert mgr.try_acquire("1", req_id="req-1") is True
    assert mgr.is_locked("1") is True
    assert "1" in mgr.get_active_devices()
    owner = mgr.get_owner("1")
    assert owner is not None
    assert owner["req_id"] == "req-1"

    # Secondary acquire must fail fast
    assert mgr.try_acquire("1", req_id="req-2") is False

    mgr.release("1")
    assert not mgr.is_locked("1")
    assert mgr.get_active_devices() == []
    assert mgr.get_owner("1") is None

    # Re-acquire must succeed
    assert mgr.try_acquire("1", req_id="req-3") is True
    mgr.release("1")


def test_device_lock_manager_normalization():
    mgr = DeviceLockManager()
    # Integer vs string normalization
    assert mgr.try_acquire(1, req_id="num-1") is True
    assert mgr.is_locked("1") is True
    assert mgr.try_acquire("1", req_id="str-1") is False
    mgr.release("1")
    assert not mgr.is_locked(1)

    # Invalid device values
    with pytest.raises(ValueError):
        mgr.try_acquire(None)
    with pytest.raises(ValueError):
        mgr.try_acquire("   ")


def test_device_lock_manager_multiple_independent_devices():
    mgr = DeviceLockManager()
    assert mgr.try_acquire("dev-A") is True
    assert mgr.try_acquire("dev-B") is True
    assert mgr.try_acquire("dev-C") is True

    assert set(mgr.get_active_devices()) == {"dev-A", "dev-B", "dev-C"}

    mgr.release("dev-B")
    assert set(mgr.get_active_devices()) == {"dev-A", "dev-C"}
    mgr.release("dev-A")
    mgr.release("dev-C")
    assert mgr.get_active_devices() == []


def test_device_lock_manager_context_manager():
    mgr = DeviceLockManager()
    with mgr.lock_device("dev-1", req_id="ctx-1"):
        assert mgr.is_locked("dev-1")
        with pytest.raises(DeviceBusyError) as exc_info:
            with mgr.lock_device("dev-1", req_id="ctx-2"):
                pass
        assert exc_info.value.device == "dev-1"
        assert exc_info.value.req_id == "ctx-2"

    assert not mgr.is_locked("dev-1")


# ==============================================================================
# TCP Server Integration Tests for Ticket 02
# ==============================================================================


def test_tcp_device_field_in_request_and_response(running_server):
    """Verify commands target specific devices via 'device' and echo it on success."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        req = {"req_id": "req-dev-1", "cmd": "ping", "device": "station-1"}
        send_json(sock, req)
        resp = recv_json(sock)

        assert resp["req_id"] == "req-dev-1"
        assert resp["status"] == "ok"
        assert resp["data"] == "pong"
        assert resp["device"] == "station-1"


def test_tcp_immediate_fail_fast_on_same_device_conflict(running_server):
    """Verify that concurrent commands targeting the same device fail fast with busy."""
    host, port = running_server.server_address

    started_event = threading.Event()

    def slow_device_handler(req):
        started_event.set()
        time.sleep(0.4)
        return {"action": "done", "device": req.get("device")}

    running_server.register_handler("slow_work", slow_device_handler)

    client1_resp = {}
    client2_resp = {}
    client2_duration = None

    def client1_worker():
        with socket.create_connection((host, port), timeout=3.0) as sock:
            send_json(sock, {"req_id": "req-slow-1", "cmd": "slow_work", "device": "1"})
            client1_resp.update(recv_json(sock))

    def client2_worker():
        # Wait until client1 has actually started executing on the server
        assert started_event.wait(timeout=2.0)
        t_start = time.time()
        with socket.create_connection((host, port), timeout=2.0) as sock:
            send_json(sock, {"req_id": "req-002", "cmd": "slow_work", "device": "1"})
            client2_resp.update(recv_json(sock))
        nonlocal client2_duration
        client2_duration = time.time() - t_start

    t1 = threading.Thread(target=client1_worker)
    t2 = threading.Thread(target=client2_worker)

    t1.start()
    t2.start()

    t2.join(timeout=2.0)
    t1.join(timeout=2.0)

    # Client 2 must receive immediate busy response
    assert client2_duration is not None
    assert client2_duration < 0.25, f"Expected fast response, but took {client2_duration}s"
    assert client2_resp["req_id"] == "req-002"
    assert client2_resp["status"] == "busy"
    assert client2_resp["error"] == "device_busy"
    assert client2_resp["device"] == "1"

    # Client 1 should complete normally
    assert client1_resp["req_id"] == "req-slow-1"
    assert client1_resp["status"] == "ok"
    assert client1_resp["device"] == "1"


def test_tcp_subsequent_commands_processed_normally_after_active_command_completes(running_server):
    """Verify that when active command on device A completes, subsequent commands on device A succeed."""
    host, port = running_server.server_address

    def short_work(req):
        time.sleep(0.1)
        return "step_ok"

    running_server.register_handler("short_work", short_work)

    with socket.create_connection((host, port), timeout=2.0) as sock:
        # First command
        send_json(sock, {"req_id": "step-1", "cmd": "short_work", "device": "station-A"})
        r1 = recv_json(sock)
        assert r1["req_id"] == "step-1"
        assert r1["status"] == "ok"
        assert r1["data"] == "step_ok"
        assert r1["device"] == "station-A"

        # Subsequent command targeting same device immediately succeeds
        send_json(sock, {"req_id": "step-2", "cmd": "short_work", "device": "station-A"})
        r2 = recv_json(sock)
        assert r2["req_id"] == "step-2"
        assert r2["status"] == "ok"
        assert r2["data"] == "step_ok"
        assert r2["device"] == "station-A"


def test_tcp_concurrent_different_devices_execute_without_contention(running_server):
    """Verify concurrent requests to different devices execute simultaneously without blocking."""
    host, port = running_server.server_address

    def parallel_work(req):
        time.sleep(0.35)
        return f"result_for_{req.get('device')}"

    running_server.register_handler("parallel_work", parallel_work)

    devices = ["dev-1", "dev-2", "dev-3", "dev-4"]
    results = {}
    barrier = threading.Barrier(len(devices))

    def worker(dev: str):
        with socket.create_connection((host, port), timeout=3.0) as sock:
            barrier.wait()
            send_json(sock, {"req_id": f"req-{dev}", "cmd": "parallel_work", "device": dev})
            resp = recv_json(sock)
            results[dev] = resp

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(d,)) for d in devices]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)
    total_time = time.time() - t_start

    # If they were serialized, it would take 4 * 0.35 = 1.4s.
    # Concurrent execution should complete well under 0.8s.
    assert total_time < 0.8, f"Expected parallel execution under 0.8s, got {total_time:.2f}s"
    assert len(results) == len(devices)
    for dev in devices:
        assert results[dev]["status"] == "ok"
        assert results[dev]["device"] == dev
        assert results[dev]["data"] == f"result_for_{dev}"


def test_tcp_device_agnostic_commands_not_blocked(running_server):
    """Verify global commands like ping and status are not blocked while a device is locked."""
    host, port = running_server.server_address

    started_event = threading.Event()

    def long_task(req):
        started_event.set()
        time.sleep(0.4)
        return "task_done"

    running_server.register_handler("long_task", long_task)

    client1_done = threading.Event()

    def long_worker():
        with socket.create_connection((host, port), timeout=2.0) as sock:
            send_json(sock, {"req_id": "long-1", "cmd": "long_task", "device": "busy-station"})
            recv_json(sock)
        client1_done.set()

    t = threading.Thread(target=long_worker)
    t.start()

    assert started_event.wait(timeout=2.0)

    # Independent client connects and sends ping and status
    with socket.create_connection((host, port), timeout=2.0) as sock:
        t0 = time.time()
        send_json(sock, {"req_id": "agnostic-ping", "cmd": "ping"})
        r_ping = recv_json(sock)
        ping_latency = time.time() - t0

        assert ping_latency < 0.1
        assert r_ping["status"] == "ok"
        assert r_ping["data"] == "pong"

        # Status should show busy device
        send_json(sock, {"req_id": "agnostic-status", "cmd": "status"})
        r_status = recv_json(sock)
        assert r_status["status"] == "ok"
        assert "busy-station" in r_status["data"]["busy_devices"]

    t.join(timeout=2.0)


def test_tcp_exception_releases_device_lock(running_server):
    """Verify that exceptions during handler execution release the device lock properly."""
    host, port = running_server.server_address

    def buggy_handler(req):
        raise RuntimeError("Something failed in hardware communication")

    running_server.register_handler("buggy_cmd", buggy_handler)

    with socket.create_connection((host, port), timeout=2.0) as sock:
        send_json(sock, {"req_id": "err-1", "cmd": "buggy_cmd", "device": "faulty-dev"})
        resp = recv_json(sock)
        assert resp["req_id"] == "err-1"
        assert resp["status"] == "error"
        assert resp["error"] == "internal_error"
        assert resp["device"] == "faulty-dev"

        # Device should be unlocked immediately
        send_json(sock, {"req_id": "rec-1", "cmd": "ping", "device": "faulty-dev"})
        rec_resp = recv_json(sock)
        assert rec_resp["req_id"] == "rec-1"
        assert rec_resp["status"] == "ok"
        assert rec_resp["device"] == "faulty-dev"


def test_tcp_numeric_device_conflict_normalization(running_server):
    """Verify integer device 1 conflicts with string '1' (normalization)."""
    host, port = running_server.server_address

    started_event = threading.Event()

    def slow_worker(req):
        started_event.set()
        time.sleep(0.3)
        return "ok"

    running_server.register_handler("slow_norm", slow_worker)

    client2_resp = {}

    def worker_num():
        with socket.create_connection((host, port), timeout=2.0) as sock:
            send_json(sock, {"req_id": "r-num", "cmd": "slow_norm", "device": 1})
            recv_json(sock)

    def worker_str():
        assert started_event.wait(timeout=2.0)
        with socket.create_connection((host, port), timeout=2.0) as sock:
            send_json(sock, {"req_id": "r-str", "cmd": "slow_norm", "device": "1"})
            client2_resp.update(recv_json(sock))

    t1 = threading.Thread(target=worker_num)
    t2 = threading.Thread(target=worker_str)
    t1.start()
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert client2_resp["req_id"] == "r-str"
    assert client2_resp["status"] == "busy"
    assert client2_resp["error"] == "device_busy"
    assert client2_resp["device"] == "1"
