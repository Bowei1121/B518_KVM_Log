"""Integration tests for Long-Polling Visual Verification Command (Ticket 05).

Verifies:
1. Long-polling `check` command over TCP with `timeout_sec`.
2. TCP connection remains open while evaluating frames asynchronously in the background.
3. Returns immediately with the matching result once visual conditions are met.
4. Returns `timeout` status if no match occurs within `timeout_sec`.
5. Integrates device locking (fail-fast busy on concurrent commands) and Frame Freeze Guard.
6. Multiplexing across concurrent clients without socket starvation.
7. Graceful cancellation and lock release on client socket disconnect.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_lock import DeviceLockManager
from freeze_guard import FrameFreezeGuard
from kvm_pool import KVMConnectionPool
from tcp_server import TcpJsonServer
from round_frame_consumer import MarkerState, RoundFrameGate


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

class MockKVMClient:
    """Mock JetKVM WebRTC client with controllable Presentation Timestamp (PTS) and visual results."""

    def __init__(
        self,
        initial_pts: Optional[float] = 100.0,
        initial_sequence: int = 1,
        stream_id: str = "mock-stream-01",
        frame_data: Any = "dummy_frame_data",
        initial_result: Optional[str] = None,
        initial_visual_state: Optional[str] = None,
    ) -> None:
        self.frame = frame_data
        self.frame_sequence = initial_sequence
        self.frame_presentation_time = initial_pts
        self.frame_received_monotonic = time.monotonic()
        self.stream_id = stream_id
        self.connected = True
        self.result = initial_result
        self.visual_state = initial_visual_state
        self.pattern = None
        self._pts_delta = 0.033

    def latest_frame(self) -> Tuple[Any, int, float, str, Optional[float]]:
        return (
            self.frame,
            self.frame_sequence,
            self.frame_received_monotonic,
            self.stream_id,
            self.frame_presentation_time,
        )

    def advance(self, delta_pts: Optional[float] = None) -> float:
        """Simulate arrival of a new video frame with incremented PTS."""
        step = delta_pts if delta_pts is not None else self._pts_delta
        if self.frame_presentation_time is not None:
            self.frame_presentation_time += step
        else:
            self.frame_presentation_time = step
        self.frame_sequence += 1
        self.frame_received_monotonic = time.monotonic()
        return self.frame_presentation_time

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False


def send_json(sock: socket.socket, data: Dict[str, Any]) -> None:
    payload = json.dumps(data) + "\n"
    sock.sendall(payload.encode("utf-8"))


def recv_json(sock: socket.socket, timeout: float = 5.0) -> Dict[str, Any]:
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    raw = b"".join(chunks).decode("utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if line:
            return json.loads(line)
    raise TimeoutError("No valid newline-delimited JSON received")


@pytest.fixture
def running_server_with_kvm_pool():
    """Start TcpJsonServer with mock KVM connection pool and live streams."""
    kvm_map: Dict[str, MockKVMClient] = {}

    def client_factory(host: str, password: str = ""):
        if host not in kvm_map:
            kvm_map[host] = MockKVMClient(initial_pts=10.0)
        return kvm_map[host]

    pool = KVMConnectionPool(client_factory=client_factory)
    lock_mgr = DeviceLockManager()
    freeze_guard = FrameFreezeGuard(freeze_threshold=0.2)
    server = TcpJsonServer(
        host="127.0.0.1",
        port=0,
        lock_manager=lock_mgr,
        kvm_pool=pool,
        freeze_guard=freeze_guard,
    )
    server.start()

    host, port = server.server_address
    yield server, kvm_map, (host, port)

    server.stop()
    server.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# Test Cases for Long-Polling
# ---------------------------------------------------------------------------

class TestLongPollingVisualVerification:
    """Test suite for long-polling check command lifecycle and edge cases."""

    def test_immediate_visual_match_returns_fast(self, running_server_with_kvm_pool):
        """When target pattern or result is already met, return immediately with status ok."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=10.0, initial_result="PASS")
        kvm_map["192.168.1.10"] = kvm

        # Live stream in background
        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                t0 = time.monotonic()
                send_json(
                    sock,
                    {
                        "req_id": "req-003",
                        "cmd": "check",
                        "device": "1",
                        "kvm_ip": "192.168.1.10",
                        "timeout_sec": 30,
                    },
                )
                res = recv_json(sock, timeout=3.0)
                elapsed = time.monotonic() - t0

                assert res["req_id"] == "req-003"
                assert res["status"] == "ok"
                assert res["result"] == "PASS"
                assert res["device"] == "1"
                assert "elapsed_sec" in res
                assert elapsed < 1.0
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_delayed_visual_match_holds_tcp_connection(self, running_server_with_kvm_pool):
        """TCP socket stays open while server polls, returning once condition is met in background."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=20.0, initial_result=None)
        kvm_map["192.168.1.11"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            start = time.monotonic()
            while not stop_stream.is_set():
                time.sleep(0.03)
                kvm.advance(0.033)
                # After 0.4 seconds, set visual result to PASS
                if time.monotonic() - start >= 0.4 and kvm.result is None:
                    kvm.result = "PASS"

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=5.0) as sock:
                t0 = time.monotonic()
                send_json(
                    sock,
                    {
                        "req_id": "req-delayed",
                        "cmd": "check",
                        "device": "2",
                        "kvm_ip": "192.168.1.11",
                        "timeout_sec": 10,
                        "poll_interval": 0.05,
                    },
                )
                res = recv_json(sock, timeout=5.0)
                elapsed = time.monotonic() - t0

                assert res["req_id"] == "req-delayed"
                assert res["status"] == "ok"
                assert res["result"] == "PASS"
                assert res["device"] == "2"
                assert 0.3 <= elapsed <= 2.0
                assert 0.3 <= res["elapsed_sec"] <= 2.0
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_long_polling_times_out_when_no_match(self, running_server_with_kvm_pool):
        """When no match occurs within timeout_sec, return timeout response with result NONE."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=30.0, initial_result=None)
        kvm_map["192.168.1.12"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                t0 = time.monotonic()
                send_json(
                    sock,
                    {
                        "req_id": "req-to",
                        "cmd": "check",
                        "device": "3",
                        "kvm_ip": "192.168.1.12",
                        "timeout_sec": 0.5,
                        "poll_interval": 0.05,
                    },
                )
                res = recv_json(sock, timeout=3.0)
                elapsed = time.monotonic() - t0

                assert res["req_id"] == "req-to"
                assert res["status"] == "timeout"
                assert res["result"] == "NONE"
                assert res["device"] == "3"
                assert elapsed >= 0.45
                assert res["elapsed_sec"] >= 0.5
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_device_locking_blocks_concurrent_commands_during_long_poll(self, running_server_with_kvm_pool):
        """While Client A is long-polling device 4, Client B gets immediate busy rejection."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=40.0, initial_result=None)
        kvm_map["192.168.1.13"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=5.0) as sock_a, \
                 socket.create_connection((host, port), timeout=5.0) as sock_b:

                # Client A launches long-poll for 1.0s
                send_json(
                    sock_a,
                    {
                        "req_id": "req-client-a",
                        "cmd": "check",
                        "device": "4",
                        "kvm_ip": "192.168.1.13",
                        "timeout_sec": 1.0,
                        "poll_interval": 0.05,
                    },
                )
                time.sleep(0.1)

                # Client B attempts command on same device 4
                send_json(
                    sock_b,
                    {
                        "req_id": "req-client-b",
                        "cmd": "ping",
                        "device": "4",
                    },
                )
                res_b = recv_json(sock_b, timeout=2.0)

                assert res_b["req_id"] == "req-client-b"
                assert res_b["status"] == "busy"
                assert res_b["error"] == "device_busy"
                assert res_b["device"] == "4"

                # Client A eventually times out
                res_a = recv_json(sock_a, timeout=3.0)
                assert res_a["req_id"] == "req-client-a"
                assert res_a["status"] == "timeout"
                assert res_a["result"] == "NONE"

                # Now that Client A is done, device 4 lock is released; Client B succeeds
                send_json(
                    sock_b,
                    {
                        "req_id": "req-client-b-retry",
                        "cmd": "ping",
                        "device": "4",
                    },
                )
                res_b_retry = recv_json(sock_b, timeout=2.0)
                assert res_b_retry["req_id"] == "req-client-b-retry"
                assert res_b_retry["status"] == "ok"
                assert res_b_retry["data"] == "pong"
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_freeze_guard_aborts_long_polling_immediately(self, running_server_with_kvm_pool):
        """If video stream freezes mid-poll, Frame Freeze Guard aborts check before timeout_sec."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=50.0, initial_result=None)
        kvm_map["192.168.1.14"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            start = time.monotonic()
            while not stop_stream.is_set():
                time.sleep(0.02)
                # Advance PTS for first 0.1s, then freeze completely!
                if time.monotonic() - start < 0.1:
                    kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=4.0) as sock:
                t0 = time.monotonic()
                send_json(
                    sock,
                    {
                        "req_id": "req-freeze-abort",
                        "cmd": "check",
                        "device": "5",
                        "kvm_ip": "192.168.1.14",
                        "timeout_sec": 5.0,  # Long timeout
                        "freeze_threshold": 0.2,  # Short freeze threshold
                        "poll_interval": 0.05,
                    },
                )
                res = recv_json(sock, timeout=4.0)
                elapsed = time.monotonic() - t0

                assert res["req_id"] == "req-freeze-abort"
                assert res["status"] == "error"
                assert res["error"] == "frame_frozen"
                assert res["device"] == "5"
                # Aborted well before the 5.0s timeout!
                assert elapsed < 2.0

                # Device 5 lock must be released immediately upon freeze abort
                send_json(
                    sock,
                    {
                        "req_id": "req-followup",
                        "cmd": "ping",
                        "device": "5",
                    },
                )
                res2 = recv_json(sock, timeout=2.0)
                assert res2["status"] == "ok"
                assert res2["data"] == "pong"
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_no_socket_starvation_multiplexing(self, running_server_with_kvm_pool):
        """Client 1 running a long poll on device 6 does not starve Client 2 communicating concurrently."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm_dev6 = MockKVMClient(initial_pts=60.0, initial_result=None)
        kvm_dev7 = MockKVMClient(initial_pts=70.0, initial_result="PASS")
        kvm_map["192.168.1.15"] = kvm_dev6
        kvm_map["192.168.1.16"] = kvm_dev7

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm_dev6.advance(0.033)
                kvm_dev7.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=5.0) as sock1, \
                 socket.create_connection((host, port), timeout=5.0) as sock2:

                # Client 1 begins a 2.0s long-poll on Device 6
                send_json(
                    sock1,
                    {
                        "req_id": "c1-long-poll",
                        "cmd": "check",
                        "device": "6",
                        "kvm_ip": "192.168.1.15",
                        "timeout_sec": 2.0,
                        "poll_interval": 0.05,
                    },
                )
                time.sleep(0.05)

                # Client 2 makes 5 consecutive calls on different devices / ping
                for i in range(5):
                    t_start = time.monotonic()
                    send_json(sock2, {"req_id": f"c2-ping-{i}", "cmd": "ping"})
                    r = recv_json(sock2, timeout=1.0)
                    t_dur = time.monotonic() - t_start
                    assert r["status"] == "ok"
                    assert r["data"] == "pong"
                    # Must finish in milliseconds without socket starvation!
                    assert t_dur < 0.2

                # Client 2 also queries Device 7 which is already PASS
                t_start = time.monotonic()
                send_json(
                    sock2,
                    {
                        "req_id": "c2-check-dev7",
                        "cmd": "check",
                        "device": "7",
                        "kvm_ip": "192.168.1.16",
                        "timeout_sec": 2.0,
                    },
                )
                r7 = recv_json(sock2, timeout=1.0)
                assert r7["status"] == "ok"
                assert r7["result"] == "PASS"
                assert time.monotonic() - t_start < 0.3

                # Client 1 eventually finishes long-poll
                r1 = recv_json(sock1, timeout=3.0)
                assert r1["req_id"] == "c1-long-poll"
                assert r1["status"] == "timeout"
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_client_disconnect_during_long_poll_releases_device_lock(self, running_server_with_kvm_pool):
        """When a client drops socket mid-poll, server exits poll and frees device lock."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=80.0, initial_result=None)
        kvm_map["192.168.1.17"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            sock1 = socket.create_connection((host, port), timeout=3.0)
            send_json(
                sock1,
                {
                    "req_id": "drop-req",
                    "cmd": "check",
                    "device": "8",
                    "kvm_ip": "192.168.1.17",
                    "timeout_sec": 10.0,
                    "poll_interval": 0.05,
                },
            )
            time.sleep(0.05)

            # Abruptly close socket 1
            sock1.close()
            time.sleep(0.15)

            # Client 2 connects and attempts to lock Device 8
            with socket.create_connection((host, port), timeout=3.0) as sock2:
                send_json(
                    sock2,
                    {
                        "req_id": "c2-after-drop",
                        "cmd": "ping",
                        "device": "8",
                    },
                )
                res = recv_json(sock2, timeout=2.0)
                assert res["status"] == "ok"
                assert res["data"] == "pong"
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_target_pattern_matching_lifecycle(self, running_server_with_kvm_pool):
        """Explicit target_pattern parameter matches dynamically in evaluation loop."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=90.0)
        kvm.pattern = "TESTING"
        kvm_map["192.168.1.18"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            start = time.monotonic()
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)
                if time.monotonic() - start >= 0.25:
                    kvm.pattern = "CUSTOM_PASS"

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=4.0) as sock:
                send_json(
                    sock,
                    {
                        "req_id": "req-custom-pat",
                        "cmd": "check",
                        "device": "9",
                        "kvm_ip": "192.168.1.18",
                        "target_pattern": "CUSTOM_PASS",
                        "timeout_sec": 5.0,
                        "poll_interval": 0.05,
                    },
                )
                res = recv_json(sock, timeout=4.0)

                assert res["req_id"] == "req-custom-pat"
                assert res["status"] == "ok"
                assert res["result"] == "CUSTOM_PASS"
                assert res["device"] == "9"
                assert 0.2 <= res["elapsed_sec"] <= 2.0
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_invalid_timeout_and_missing_kvm_error_handling(self, running_server_with_kvm_pool):
        """Server rejects negative timeout and missing KVM cleanly."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        kvm = MockKVMClient(initial_pts=100.0)
        kvm_map["192.168.1.19"] = kvm

        with socket.create_connection((host, port), timeout=3.0) as sock:
            # 1. Negative timeout
            send_json(
                sock,
                {
                    "req_id": "err-neg-to",
                    "cmd": "check",
                    "device": "10",
                    "kvm_ip": "192.168.1.19",
                    "timeout_sec": -5,
                },
            )
            r1 = recv_json(sock)
            assert r1["status"] == "error"
            assert r1["error"] == "invalid_timeout"

            # 2. Non-numeric timeout
            send_json(
                sock,
                {
                    "req_id": "err-str-to",
                    "cmd": "check",
                    "device": "10",
                    "kvm_ip": "192.168.1.19",
                    "timeout_sec": "not-a-number",
                },
            )
            r2 = recv_json(sock)
            assert r2["status"] == "error"
            assert r2["error"] == "invalid_timeout"

            # 3. Missing kvm_ip
            send_json(
                sock,
                {
                    "req_id": "err-no-kvm",
                    "cmd": "check",
                    "device": "10",
                },
            )
            r3 = recv_json(sock)
            assert r3["status"] == "error"
            assert r3["error"] == "missing_kvm"

    def test_dfu_station_long_polling_transition(self, running_server_with_kvm_pool):
        """DFU station long-polling holds until round-frame marker transitions to complete."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool
        from test_round_frame_consumer import render_frame

        # Start with MONITORING frame
        monitoring_frame = render_frame(MarkerState.MONITORING, ["WAITING", "WAITING"])
        complete_frame = render_frame(MarkerState.COMPLETE, ["PASS", "PASS"])

        kvm = MockKVMClient(initial_pts=110.0, frame_data=monitoring_frame)
        kvm_map["192.168.1.20"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            start = time.monotonic()
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)
                if time.monotonic() - start >= 0.3:
                    kvm.frame = complete_frame

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=4.0) as sock:
                send_json(
                    sock,
                    {
                        "req_id": "req-dfu-lp",
                        "cmd": "check",
                        "station": "DFU",
                        "device": "20",
                        "kvm_ip": "192.168.1.20",
                        "timeout_sec": 4.0,
                        "poll_interval": 0.05,
                    },
                )
                res = recv_json(sock, timeout=4.0)

                assert res["req_id"] == "req-dfu-lp"
                assert res["status"] == "ok"
                assert res["result"] == "PASS"
                assert res["station"] == "DFU"
                assert res["device"] == "20"
                assert 0.25 <= res["elapsed_sec"] <= 2.5
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_multi_client_parallel_long_polling_distinct_devices(self, running_server_with_kvm_pool):
        """Multiple stations long-polling concurrently complete independently at their own match times."""
        server, kvm_map, (host, port) = running_server_with_kvm_pool

        # 3 stations with different completion delays
        delays = {"30": 0.2, "31": 0.4, "32": 0.6}
        kvms = {}
        for dev, delay in delays.items():
            k = MockKVMClient(initial_pts=200.0 + float(dev))
            kvms[dev] = k
            kvm_map[f"192.168.1.{dev}"] = k

        stop_stream = threading.Event()

        def stream_worker():
            start = time.monotonic()
            while not stop_stream.is_set():
                time.sleep(0.02)
                now = time.monotonic()
                for dev, k in kvms.items():
                    k.advance(0.033)
                    if now - start >= delays[dev] and k.result is None:
                        k.result = "PASS"

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        results = {}

        def client_thread(dev: str):
            with socket.create_connection((host, port), timeout=5.0) as sock:
                send_json(
                    sock,
                    {
                        "req_id": f"req-par-{dev}",
                        "cmd": "check",
                        "device": dev,
                        "kvm_ip": f"192.168.1.{dev}",
                        "timeout_sec": 3.0,
                        "poll_interval": 0.04,
                    },
                )
                res = recv_json(sock, timeout=5.0)
                results[dev] = res

        threads = [threading.Thread(target=client_thread, args=(d,)) for d in delays]
        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=4.0)

            assert len(results) == 3
            for dev in delays:
                assert results[dev]["status"] == "ok"
                assert results[dev]["result"] == "PASS"
                assert results[dev]["device"] == dev
                assert delays[dev] - 0.1 <= results[dev]["elapsed_sec"] <= delays[dev] + 1.0
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

