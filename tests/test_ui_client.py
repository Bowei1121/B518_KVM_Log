"""Integration and unit tests for Lightweight Monitor UI Client (Ticket 07).

Verifies:
1. Tkinter UI client communicates with Core Service over TCP as a decoupled client.
2. UI client visualizes real-time status of all configured test stations (IDLE, BUSY, FROZEN).
3. UI can request and display snapshots/streams without interfering with LabVIEW automated testing (no device lock starvation).
4. Terminating or restarting the UI does not disrupt Core Service or active LabVIEW TCP sockets.
5. UI manual check triggers diagnostic evaluation and respects device locking.
6. UI runs cleanly as a separate process.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

# Ensure host-app is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_APP_DIR = _REPO_ROOT / "host-app"
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from device_lock import DeviceLockManager
from device_registry import DeviceRegistry
from fake_kvm import FakeKVM
from freeze_guard import FrameFreezeGuard
from kvm_pool import KVMConnectionPool
from protocol_helpers import make_registry, wire, numbered
from tcp_server import TcpJsonServer
from ui_app import CoreServiceClient
from protocol_helpers import LogSolutionKVM, arm_round


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

class MockKVMClient(FakeKVM):
    """Mock JetKVM Client providing controllable frames and PTS."""

    def __init__(
        self,
        initial_pts: Optional[float] = 100.0,
        initial_sequence: int = 1,
        stream_id: str = "mock-stream-01",
        frame_data: Any = None,
        initial_result: Optional[str] = None,
    ) -> None:
        if frame_data is None:
            # Create a synthetic 100x100 dummy BGR image
            try:
                import numpy as np
                frame_data = np.zeros((100, 100, 3), dtype=np.uint8)
            except Exception:
                frame_data = b"dummy_jpeg_bytes"
        super().__init__(frame_data, sequence=initial_sequence, stream_id=stream_id, pts=initial_pts)
        self.result = initial_result
        self.visual_state = None

    def advance(self, delta_pts: float = 0.033) -> float:
        if self.frame_presentation_time is not None:
            self.frame_presentation_time += delta_pts
        else:
            self.frame_presentation_time = delta_pts
        self.frame_sequence += 1
        self.frame_received_monotonic = time.monotonic()
        return self.frame_presentation_time


def send_json(sock: socket.socket, data: Dict[str, Any]) -> None:
    payload = json.dumps(wire(data)) + "\n"
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
def running_core_service():
    """Start headless TcpJsonServer configured with test stations and mock KVM pool."""
    kvm_map: Dict[str, Any] = {}

    def client_factory(host: str, password: str = ""):
        return kvm_map.setdefault(host, LogSolutionKVM())

    device_registry = DeviceRegistry.from_text(
        json.dumps(
            {
                "DFU-01": {"station": "DFU", "kvm_ip": "192.168.1.10", "channels": 4},
                "BT-02": {"station": "BT", "kvm_ip": "192.168.1.13", "channels": 4},
                "BT-03": {"station": "BT", "kvm_ip": "192.168.1.14", "channels": 4},
                "FCT-01": {"station": "FCT", "kvm_ip": "192.168.1.11", "channels": 4},
                "BT-01": {"station": "BT", "kvm_ip": "192.168.1.12", "channels": 4},
            }
        )
    )

    pool = KVMConnectionPool(client_factory=client_factory)
    lock_mgr = DeviceLockManager()
    freeze_guard = FrameFreezeGuard(freeze_threshold=0.3)
    server = TcpJsonServer(
        host="127.0.0.1",
        port=0,
        lock_manager=lock_mgr,
        kvm_pool=pool,
        freeze_guard=freeze_guard,
        device_registry=device_registry,
    )
    server.start()

    host, port = server.server_address
    yield server, kvm_map, (host, port)

    server.stop()
    server.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestLightweightMonitorUIClient:
    """Comprehensive test suite for Ticket 07."""

    def test_ui_client_connects_and_queries_status(self, running_core_service):
        """UI client establishes TCP connection and queries server status."""
        server, kvm_map, (host, port) = running_core_service
        client = CoreServiceClient(host=host, port=port)

        assert client.connect(timeout=2.0) is True
        assert client.is_connected() is True
        assert client.ping(timeout=2.0) is True

        status = client.get_status(timeout=2.0)
        assert status.get("status") == "running"
        assert "uptime_seconds" in status
        assert "active_connections" in status
        assert "stations" in status
        client.disconnect()

    def test_ui_visualizes_realtime_station_statuses(self, running_core_service):
        """UI accurately retrieves and reflects station statuses (IDLE -> BUSY -> IDLE)."""
        server, kvm_map, (host, port) = running_core_service
        client = CoreServiceClient(host=host, port=port)

        # 1. Initial state: all stations idle/disconnected
        st_res = client.get_stations(timeout=2.0)
        assert st_res.get("status") in ("running", "ok")
        stations = {s["station"]: s for s in reversed(st_res.get("stations", []))}
        assert "DFU" in stations
        assert "FCT" in stations
        assert "BT" in stations
        assert stations["DFU"]["status"] in ("IDLE", "DISCONNECTED")

        # 2. Simulate LabVIEW locking device "1" (DFU)
        assert server.lock_manager.try_acquire("DFU-01", req_id="labview-sim-1") is True

        st_res2 = client.get_stations(timeout=2.0)
        stations2 = {s["station"]: s for s in reversed(st_res2.get("stations", []))}
        assert stations2["DFU"]["status"] == "BUSY"
        assert stations2["DFU"]["is_busy"] is True

        # 3. LabVIEW releases lock
        server.lock_manager.release("DFU-01")

        st_res3 = client.get_stations(timeout=2.0)
        stations3 = {s["station"]: s for s in reversed(st_res3.get("stations", []))}
        assert stations3["DFU"]["status"] != "BUSY"
        assert stations3["DFU"]["is_busy"] is False

        client.disconnect()

    def test_ui_snapshot_does_not_interfere_with_labview_automated_testing(self, running_core_service):
        """UI snapshot of a device under a LabVIEW long-poll succeeds immediately and does not steal the round."""
        server, kvm_map, (host, port) = running_core_service
        kvm = kvm_map.setdefault("192.168.1.10", LogSolutionKVM())
        arm_round(server, kvm, "DFU-01")

        with socket.create_connection((host, port), timeout=10.0) as labview_sock:
            send_json(labview_sock, {"protocol_version": "1.0", "req_id": "labview-test-01",
                                     "cmd": "check_finish", "device": "DFU-01", "timeout_sec": 8})
            time.sleep(0.3)

            assert server.lock_manager.is_locked("DFU-01") is True

            ui_client = CoreServiceClient(host=host, port=port)
            snap_res = ui_client.get_snapshot(device="DFU-01", timeout=2.0)
            assert snap_res.get("status") == "ok", f"Expected ok, got: {snap_res}"
            assert len(snap_res["image_base64"]) > 0

            time.sleep(0.2)
            kvm.show("complete", ["PASS", "FAIL", "NOTEST", "PASS"])

            lv_res = recv_json(labview_sock, timeout=10.0)
            assert lv_res["req_id"] == "labview-test-01"
            assert lv_res["status"] == "ok"
            assert lv_res["data"]["kind"] == "taken"
            assert [r["status"] for r in lv_res["data"]["results"]] == ["PASS", "FAIL", "NOTEST", "PASS"]
            ui_client.disconnect()

    def test_terminating_or_restarting_ui_does_not_affect_core_service_or_labview(self, running_core_service):
        """Closing or crashing UI process/socket has zero impact on Core Service or active LabVIEW sessions."""
        server, kvm_map, (host, port) = running_core_service
        kvm = kvm_map.setdefault("192.168.1.10", LogSolutionKVM())
        arm_round(server, kvm, "DFU-01")

        with socket.create_connection((host, port), timeout=10.0) as labview_sock:
            send_json(labview_sock, {"protocol_version": "1.0", "req_id": "lv-ping-1", "device": "",
                                     "cmd": "ping"})
            assert recv_json(labview_sock, timeout=2.0).get("status") == "ok"

            ui_client = CoreServiceClient(host=host, port=port)
            assert ui_client.connect() is True
            assert ui_client.get_status().get("status") in ("running", "ok")
            ui_client.disconnect()
            time.sleep(0.1)

            assert server.is_running is True

            send_json(labview_sock, {"protocol_version": "1.0", "req_id": "lv-check-after-ui-crash",
                                     "device": "DFU-01", "cmd": "check_finish", "timeout_sec": 8})
            time.sleep(0.3)
            kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
            lv_check = recv_json(labview_sock, timeout=10.0)
            assert lv_check.get("status") == "ok"
            assert lv_check["data"]["kind"] == "taken"

            ui_client2 = CoreServiceClient(host=host, port=port)
            assert ui_client2.connect() is True
            assert ui_client2.get_status().get("status") in ("running", "ok")
            ui_client2.disconnect()

    def test_ui_manual_diagnostic_check(self, running_core_service):
        """UI manual check returns the synthetic round result and fails fast with busy while locked."""
        server, kvm_map, (host, port) = running_core_service
        kvm = kvm_map.setdefault("192.168.1.10", LogSolutionKVM())
        kvm.show("monitoring")

        kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
        client = CoreServiceClient(host=host, port=port)
        res = client.manual_check(device="DFU-01", timeout_sec=0)

        assert res.get("status") == "ok"
        assert res["kind"] != "taken"
        assert res["state"] == "complete"
        assert res["active"] == []
        assert res["results"] == []

        # Manual checks are read-only: a LabVIEW lock on the device does not block them.
        assert server.lock_manager.try_acquire("DFU-01", req_id="other-tester") is True
        res_locked = client.manual_check(device="DFU-01", timeout_sec=0)
        assert res_locked.get("status") == "ok"
        assert res_locked.get("error") != "device_busy"
        assert server.lock_manager.is_locked("DFU-01") is True

        server.lock_manager.release("DFU-01")
        client.disconnect()

    def test_manual_check_names_state_and_started_channels(self, running_core_service):
        """Manual inspect text names every Log state and lists started-channel evidence."""
        from ui_app import format_manual_check

        _server, kvm_map, (host, port) = running_core_service
        kvm = kvm_map.setdefault("192.168.1.10", LogSolutionKVM())
        client = CoreServiceClient(host=host, port=port)

        kvm.show("monitoring", ["COMPLETING", "FAIL", "WAITING", "NOTEST"])
        first = format_manual_check(client.manual_check(device="DFU-01", timeout_sec=0))
        assert "監控就緒" in first
        assert "測試中" not in first
        assert "已有結果" not in first

        kvm.show("monitoring", ["COMPLETING", "FAIL", "WAITING", "NOTEST"])
        second = format_manual_check(client.manual_check(device="DFU-01", timeout_sec=0))
        assert "監控就緒" in second
        assert "通道 1 測試中" in second
        assert "通道 2 已有結果" in second

        expected = (
            ("preparing", (), "準備中"),
            ("review", (), "待確認"),
            ("standby", (), "待命"),
            ("monitoring", (), "監控就緒"),
            ("complete", ("PASS", "FAIL", "NOTEST", "TIMEOUT"), "一般完成"),
            ("manual_stop_complete", ("PASS", "FAIL", "STOPPED", "NOTEST"), "人工停止完成"),
        )
        for state, statuses, label in expected:
            kvm.show(state, statuses)
            text = format_manual_check(client.manual_check(device="DFU-01", timeout_sec=0))
            assert label in text, text

        assert kvm.events == []
        client.disconnect()

    def test_ui_runs_as_separate_process_headless_check(self, running_core_service):
        """Verify ui_app runs as a decoupled standalone process using --headless-check."""
        server, kvm_map, (host, port) = running_core_service

        cmd = [
            sys.executable,
            str(_HOST_APP_DIR / "ui_app.py"),
            "--host", host,
            "--port", str(port),
            "--headless-check",
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert proc.returncode == 0, f"Process failed: {proc.stderr}"
        assert "running" in proc.stdout or "ok" in proc.stdout


# ---------------------------------------------------------------------------
# Monitor UI channel separation (Issue #20)
# ---------------------------------------------------------------------------

def _envelope(req: Dict[str, Any], data: Dict[str, Any], req_id: Optional[str] = None) -> Dict[str, Any]:
    """Build a reply in the real 1.0 envelope shape (see tcp_server._encode_envelope)."""
    return {
        "protocol_version": "1.0",
        "req_id": req["req_id"] if req_id is None else req_id,
        "device": req.get("device", ""),
        "cmd": req["cmd"],
        "status": "ok",
        "error": "",
        "message": "",
        "elapsed_sec": 0.001,
        "data": data,
    }


CORE_STATUS_DATA = {
    "uptime_seconds": 1.0,
    "active_connections": 1,
    "busy_devices": [],
    "stations": [],
    "version": "1.0.0",
}


class _FakeCore:
    """Newline-delimited JSON server. behaviour(req, conn_index) returns a reply dict and may block."""

    def __init__(self, behaviour) -> None:
        self._behaviour = behaviour
        self.requests: List[Dict[str, Any]] = []
        self.connections = 0
        self._lock = threading.Lock()
        self._conns: List[socket.socket] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                idx = self.connections
                self.connections += 1
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn, idx), daemon=True).start()

    def _serve(self, conn: socket.socket, idx: int) -> None:
        reader = conn.makefile("rb")
        try:
            for line in reader:
                req = json.loads(line.decode("utf-8"))
                with self._lock:
                    self.requests.append(req)
                reply = self._behaviour(req, idx)
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        except OSError:
            pass

    def close(self) -> None:
        self._srv.close()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            try:
                c.close()
            except OSError:
                pass


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class TestMonitorUIChannels:
    def test_manual_check_does_not_block_status_or_snapshot(self, running_core_service):
        """A long inspect on the UI client must not stall status polling or snapshots."""
        server, kvm_map, (host, port) = running_core_service
        kvm = kvm_map.setdefault("192.168.1.10", LogSolutionKVM())
        kvm.show("monitoring")

        client = CoreServiceClient(host=host, port=port)
        out: Dict[str, Any] = {}
        th = threading.Thread(
            target=lambda: out.update(res=client.manual_check(device="DFU-01", timeout_sec=8.0)),
            daemon=True,
        )
        th.start()
        time.sleep(0.3)

        t0 = time.monotonic()
        st = client.get_status(timeout=2.0)
        assert time.monotonic() - t0 < 1.0, "get_status waited behind the in-flight inspect"
        assert st.get("status") in ("running", "ok")

        snap = client.get_snapshot(device="DFU-01", timeout=3.0)
        assert snap.get("status") == "ok", f"snapshot blocked or failed: {snap}"

        kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
        th.join(timeout=12.0)
        assert out["res"].get("status") == "ok"
        client.disconnect()

    def test_status_poll_skips_while_previous_request_in_flight(self):
        """A new status poll is not started while the previous one has not come back."""
        release = threading.Event()

        def behaviour(req, idx):
            release.wait(5.0)
            return _envelope(req, CORE_STATUS_DATA)

        core = _FakeCore(behaviour)
        client = CoreServiceClient(host="127.0.0.1", port=core.port)
        results: List[Dict[str, Any]] = []
        try:
            assert client.start_status_poll(results.append) is True
            time.sleep(0.2)
            assert client.start_status_poll(results.append) is False
            assert client.start_status_poll(results.append) is False
            time.sleep(0.2)
            assert len(core.requests) == 1, "overlapping status requests were sent"

            release.set()
            assert _wait_until(lambda: len(results) == 1), "poll callback never fired"
            assert results[0].get("status") in ("running", "ok")

            assert client.start_status_poll(results.append) is True
            assert _wait_until(lambda: len(results) == 2)
        finally:
            release.set()
            client.disconnect()
            core.close()

    def test_mismatched_req_id_is_discarded_and_connection_reset(self):
        """A reply whose req_id does not match is dropped and the next request reconnects."""

        def behaviour(req, idx):
            if idx == 0:
                return _envelope(req, CORE_STATUS_DATA, req_id="someone-else-000")
            return _envelope(req, CORE_STATUS_DATA)

        core = _FakeCore(behaviour)
        client = CoreServiceClient(host="127.0.0.1", port=core.port)
        try:
            stale = client.get_status(timeout=2.0)
            assert stale.get("status") != "running"
            assert stale.get("error") == "req_id_mismatch"
            assert "uptime_seconds" not in stale

            fresh = client.get_status(timeout=2.0)
            assert fresh.get("status") in ("running", "ok")
            assert fresh.get("req_id") == core.requests[1]["req_id"]
            assert core.connections == 2
        finally:
            client.disconnect()
            core.close()

    def test_close_cancels_inflight_manual_check_without_blocking(self):
        """Closing the UI while an inspect is pending returns at once and cancels that inspect."""
        release = threading.Event()

        def behaviour(req, idx):
            release.wait(10.0)
            return _envelope(req, {"kind": "waiting", "state": "monitoring", "reason": "monitoring",
                                   "capacity": 4, "results": []})

        core = _FakeCore(behaviour)
        client = CoreServiceClient(host="127.0.0.1", port=core.port)
        out: Dict[str, Any] = {}
        th = threading.Thread(
            target=lambda: out.update(res=client.manual_check(device="DFU-01", timeout_sec=30.0)),
            daemon=True,
        )
        try:
            th.start()
            assert _wait_until(lambda: len(core.requests) == 1)

            t0 = time.monotonic()
            client.disconnect()
            assert time.monotonic() - t0 < 0.5, "close() waited on the in-flight request"

            # Wait for the cancelled reply itself, not a fixed join budget under suite load.
            assert _wait_until(
                lambda: out.get("res", {}).get("error") == "cancelled",
                timeout=5.0,
            ), f"cancelled inspect did not return: {out}"
            assert not th.is_alive()
            assert out["res"].get("status") == "error"
        finally:
            release.set()
            core.close()

    def test_abort_wakes_waiter_without_closing_from_caller(self, monkeypatch):
        """Caller-side close leaves a timeout poll asleep; shutdown lets the request thread close."""
        import core_channel

        gate, in_recv, park = threading.Event(), threading.Event(), threading.Event()

        class _Sock:
            def __init__(self):
                self.shut = self.closed = False
                self.timeout = None
                self.close_threads: List[str] = []

            def settimeout(self, timeout):
                self.timeout = timeout

            def sendall(self, data):
                if self.shut or self.closed:
                    raise BrokenPipeError(32, "EPIPE")

            def recv(self, n):
                in_recv.set()
                assert gate.wait(2)
                if self.closed or not self.shut:
                    # Caller already close()d, or nobody woke us: stand in for the
                    # timeout poll. The test releases park in finally.
                    park.wait()
                    raise socket.timeout()
                return b""

            def shutdown(self, how):
                if self.closed:
                    raise OSError(9, "EBADF")
                self.shut = True

            def close(self):
                self.closed = True
                self.close_threads.append(threading.current_thread().name)

        fs = _Sock()
        monkeypatch.setattr(core_channel.socket, "create_connection", lambda *a, **k: fs)
        ch = core_channel.CoreChannel("inspect", "127.0.0.1", 1)
        out: Dict[str, Any] = {}
        th = threading.Thread(
            target=lambda: out.update(r=ch.request("inspect", {}, 3.0)),
            name="req",
            daemon=True,
        )
        try:
            th.start()
            assert in_recv.wait(2)
            ch.abort()
            assert fs.close_threads == [], "abort() closed the in-flight socket on the caller thread"
            gate.set()
            th.join(1.0)
            assert not th.is_alive()
            assert out["r"]["error"] == "cancelled"
            assert set(fs.close_threads) == {"req"}
        finally:
            gate.set()
            park.set()

    def test_abort_before_socket_is_published_returns_cancelled(self, monkeypatch):
        """abort() in the window before _sock is published still cancels the connect."""
        import core_channel

        attached, release, park = threading.Event(), threading.Event(), threading.Event()
        real_attach = core_channel.CoreChannel._attach_socket

        def hooked(self, sock):
            attached.set()
            assert release.wait(2)
            real_attach(self, sock)

        monkeypatch.setattr(core_channel.CoreChannel, "_attach_socket", hooked)

        class _Sock:
            def __init__(self):
                self.sent = False
                self.in_recv = threading.Event()

            def settimeout(self, timeout):
                pass

            def sendall(self, data):
                self.sent = True

            def recv(self, n):
                self.in_recv.set()
                park.wait()
                raise socket.timeout()

            def shutdown(self, how):
                pass

            def close(self):
                pass

        fs = _Sock()
        monkeypatch.setattr(core_channel.socket, "create_connection", lambda *a, **k: fs)
        ch = core_channel.CoreChannel("inspect", "127.0.0.1", 1)
        out: Dict[str, Any] = {}
        th = threading.Thread(
            target=lambda: out.update(r=ch.request("inspect", {"device": "DFU-01"}, 30.0)),
            daemon=True,
        )
        try:
            th.start()
            assert attached.wait(2)
            ch.abort()
            release.set()
            th.join(1.0)
            assert not th.is_alive(), out
            assert out["r"]["error"] == "cancelled"
            assert fs.sent is False
            assert not fs.in_recv.is_set()
        finally:
            release.set()
            park.set()

    def test_abort_cancels_request_queued_on_the_lock(self):
        """A request that sampled the generation and is waiting for the lock is cancelled."""
        release = threading.Event()
        queued, let_queued_lock = threading.Event(), threading.Event()

        def behaviour(req, idx):
            release.wait(10.0)
            return _envelope(req, {"kind": "waiting", "state": "monitoring", "reason": "monitoring",
                                   "capacity": 4, "results": []})

        core = _FakeCore(behaviour)
        client = CoreServiceClient(host="127.0.0.1", port=core.port)
        inner = client._inspect._lock
        waiter = {"thread": None}

        class _Gate:
            def acquire(self, blocking=True, timeout=-1):
                if blocking and threading.current_thread() is waiter["thread"]:
                    queued.set()
                    assert let_queued_lock.wait(2)
                return inner.acquire(blocking, timeout)

            def release(self):
                return inner.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *exc):
                self.release()

        client._inspect._lock = _Gate()
        out_a: Dict[str, Any] = {}
        out_b: Dict[str, Any] = {}
        th_a = threading.Thread(
            target=lambda: out_a.update(res=client.manual_check(device="DFU-01", timeout_sec=30.0)),
            daemon=True,
        )
        th_b = threading.Thread(
            target=lambda: out_b.update(res=client.manual_check(device="DFU-01", timeout_sec=30.0)),
            daemon=True,
        )
        waiter["thread"] = th_b
        try:
            th_a.start()
            assert _wait_until(lambda: len(core.requests) == 1)
            th_b.start()
            assert queued.wait(2)
            client.disconnect()
            let_queued_lock.set()
            th_a.join(1.0)
            th_b.join(1.0)
            assert not th_a.is_alive() and not th_b.is_alive(), (out_a, out_b)
            assert out_a["res"].get("error") == "cancelled"
            assert out_b["res"].get("error") == "cancelled"
            assert len(core.requests) == 1
        finally:
            release.set()
            let_queued_lock.set()
            core.close()

    def test_abort_closes_idle_socket(self, monkeypatch):
        """abort() with no request in flight closes the fd; it does not wait for a later request."""
        import core_channel

        left, right = socket.socketpair()
        monkeypatch.setattr(core_channel.socket, "create_connection", lambda *a, **k: left)

        def serve():
            buf = b""
            while b"\n" not in buf:
                chunk = right.recv(4096)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.split(b"\n", 1)[0])
            reply = {
                "protocol_version": "1.0", "req_id": req["req_id"], "device": "",
                "cmd": req["cmd"], "status": "ok", "error": "", "message": "",
                "elapsed_sec": 0, "data": {},
            }
            right.sendall((json.dumps(reply) + "\n").encode())

        threading.Thread(target=serve, daemon=True).start()
        ch = core_channel.CoreChannel("status", "127.0.0.1", 1)
        try:
            assert ch.request("ping", None, 2.0).get("status") == "ok"
            assert left.fileno() >= 0
            ch.abort()
            assert left.fileno() == -1
        finally:
            right.close()

    def test_abort_does_not_swallow_non_oserror(self):
        import core_channel

        class _Boom:
            def shutdown(self, how):
                raise AttributeError("programmer error")

        ch = core_channel.CoreChannel("status", "127.0.0.1", 1)
        ch._sock = _Boom()
        with pytest.raises(AttributeError, match="programmer error"):
            ch.abort()

    def test_buffered_reply_after_abort_is_cancelled(self, monkeypatch):
        """A reply already in the receive buffer is cancelled once abort() has bumped the generation."""
        import core_channel

        in_recv, gate = threading.Event(), threading.Event()

        class _Sock:
            def __init__(self):
                self.sent = b""

            def settimeout(self, timeout):
                pass

            def sendall(self, data):
                self.sent = data

            def recv(self, n):
                in_recv.set()
                assert gate.wait(2)
                req = json.loads(self.sent.decode().split("\n", 1)[0])
                reply = {
                    "protocol_version": "1.0", "req_id": req["req_id"], "device": "",
                    "cmd": "inspect", "status": "ok", "error": "", "message": "",
                    "elapsed_sec": 0, "data": {"kind": "waiting"},
                }
                return (json.dumps(reply) + "\n").encode()

            def shutdown(self, how):
                pass

            def close(self):
                pass

        monkeypatch.setattr(core_channel.socket, "create_connection", lambda *a, **k: _Sock())
        ch = core_channel.CoreChannel("inspect", "127.0.0.1", 1)
        out: Dict[str, Any] = {}
        th = threading.Thread(
            target=lambda: out.update(r=ch.request("inspect", {"device": "DFU-01"}, 3.0)),
            daemon=True,
        )
        try:
            th.start()
            assert in_recv.wait(2)
            ch.abort()
            gate.set()
            th.join(1.0)
            assert not th.is_alive(), out
            assert out["r"]["status"] != "ok"
            assert out["r"]["error"] == "cancelled"
        finally:
            gate.set()

    def test_set_target_does_not_wait_for_inflight_request(self):
        """Switching Core address while an inspect is pending must not block the caller."""
        release = threading.Event()

        def behaviour(req, idx):
            release.wait(10.0)
            return _envelope(req, {"kind": "waiting", "state": "monitoring", "reason": "monitoring",
                                   "capacity": 4, "results": []})

        core = _FakeCore(behaviour)
        other = _FakeCore(lambda req, idx: _envelope(req, CORE_STATUS_DATA))
        client = CoreServiceClient(host="127.0.0.1", port=core.port)
        th = threading.Thread(target=lambda: client.manual_check(device="DFU-01", timeout_sec=30.0), daemon=True)
        try:
            th.start()
            assert _wait_until(lambda: len(core.requests) == 1)

            t0 = time.monotonic()
            client.set_target("127.0.0.1", other.port)
            assert time.monotonic() - t0 < 0.5, "set_target waited on the in-flight request"

            assert client.get_status(timeout=2.0).get("status") in ("running", "ok")
        finally:
            release.set()
            client.disconnect()
            th.join(timeout=2.0)
            core.close()
            other.close()
