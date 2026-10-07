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
from typing import Any, Dict, List, Optional, Tuple

import pytest

# Ensure host-app is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_APP_DIR = _REPO_ROOT / "host-app"
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from device_lock import DeviceLockManager
from freeze_guard import FrameFreezeGuard
from kvm_pool import KVMConnectionPool
from tcp_server import TcpJsonServer
from ui_app import CoreServiceClient


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

class MockKVMClient:
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
                self.frame = np.zeros((100, 100, 3), dtype=np.uint8)
            except Exception:
                self.frame = b"dummy_jpeg_bytes"
        else:
            self.frame = frame_data

        self.frame_sequence = initial_sequence
        self.frame_presentation_time = initial_pts
        self.frame_received_monotonic = time.monotonic()
        self.stream_id = stream_id
        self.connected = True
        self.result = initial_result
        self.visual_state = None

    def latest_frame(self) -> Tuple[Any, int, float, str, Optional[float]]:
        return (
            self.frame,
            self.frame_sequence,
            self.frame_received_monotonic,
            self.stream_id,
            self.frame_presentation_time,
        )

    def advance(self, delta_pts: float = 0.033) -> float:
        if self.frame_presentation_time is not None:
            self.frame_presentation_time += delta_pts
        else:
            self.frame_presentation_time = delta_pts
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
def running_core_service():
    """Start headless TcpJsonServer configured with test stations and mock KVM pool."""
    kvm_map: Dict[str, MockKVMClient] = {}

    def client_factory(host: str, password: str = ""):
        if host not in kvm_map:
            kvm_map[host] = MockKVMClient(initial_pts=10.0)
        return kvm_map[host]

    stations_config = [
        {"station": "DFU", "device": "1", "kvm_ip": "192.168.1.10"},
        {"station": "FCT", "device": "1", "kvm_ip": "192.168.1.11"},
        {"station": "BT", "device": "1", "kvm_ip": "192.168.1.12"},
    ]

    pool = KVMConnectionPool(client_factory=client_factory)
    lock_mgr = DeviceLockManager()
    freeze_guard = FrameFreezeGuard(freeze_threshold=0.3)
    server = TcpJsonServer(
        host="127.0.0.1",
        port=0,
        lock_manager=lock_mgr,
        kvm_pool=pool,
        freeze_guard=freeze_guard,
        stations_config=stations_config,
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
        assert st_res.get("status") == "ok"
        stations = {s["station"]: s for s in st_res.get("stations", [])}
        assert "DFU" in stations
        assert "FCT" in stations
        assert "BT" in stations
        assert stations["DFU"]["status"] in ("IDLE", "DISCONNECTED")

        # 2. Simulate LabVIEW locking device "1" (DFU)
        assert server.lock_manager.try_acquire("1", req_id="labview-sim-1") is True

        st_res2 = client.get_stations(timeout=2.0)
        stations2 = {s["station"]: s for s in st_res2.get("stations", [])}
        assert stations2["DFU"]["status"] == "BUSY"
        assert stations2["DFU"]["is_busy"] is True

        # 3. LabVIEW releases lock
        server.lock_manager.release("1")

        st_res3 = client.get_stations(timeout=2.0)
        stations3 = {s["station"]: s for s in st_res3.get("stations", [])}
        assert stations3["DFU"]["status"] != "BUSY"
        assert stations3["DFU"]["is_busy"] is False

        client.disconnect()

    def test_ui_snapshot_does_not_interfere_with_labview_automated_testing(self, running_core_service):
        """UI can request snapshots of a device currently under active LabVIEW test without interference.
        
        Verification:
        - LabVIEW starts long-poll check holding DeviceLock on device '1'.
        - UI client requests snapshot of device '1' at the same time.
        - UI snapshot succeeds immediately (does not return busy or error).
        - LabVIEW test completes normally without lock contention or disruption.
        """
        server, kvm_map, (host, port) = running_core_service
        kvm = MockKVMClient(initial_pts=50.0, initial_result=None)
        kvm_map["192.168.1.10"] = kvm

        # Frame advancement worker
        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            with socket.create_connection((host, port), timeout=5.0) as labview_sock:
                # 1. LabVIEW starts long-polling check on device 1
                send_json(
                    labview_sock,
                    {
                        "req_id": "labview-test-01",
                        "cmd": "check",
                        "device": "1",
                        "station": "BT",
                        "kvm_ip": "192.168.1.10",
                        "target_pattern": "PASS",
                        "timeout_sec": 1.5,
                        "poll_interval": 0.05,
                    },
                )
                time.sleep(0.1)

                # Verify device 1 is actively locked by LabVIEW
                assert server.lock_manager.is_locked("1") is True

                # 2. Concurrently, UI client requests snapshot of the SAME device 1
                ui_client = CoreServiceClient(host=host, port=port)
                snap_res = ui_client.get_snapshot(
                    kvm_ip="192.168.1.10",
                    device="1",
                    station="BT",
                    timeout=2.0,
                )

                # UI snapshot MUST succeed and MUST NOT return 'busy'
                assert snap_res.get("status") == "ok", f"Expected ok, got: {snap_res}"
                assert "image_base64" in snap_res
                assert snap_res.get("pts") is not None
                assert len(snap_res.get("image_base64", "")) > 0

                # 3. Simulate LabVIEW test completing: set result to PASS
                time.sleep(0.1)
                kvm.result = "PASS"

                # 4. LabVIEW receives clean test result
                lv_res = recv_json(labview_sock, timeout=3.0)
                assert lv_res["req_id"] == "labview-test-01"
                assert lv_res["status"] == "ok"
                assert lv_res["result"] == "PASS"

                ui_client.disconnect()
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_terminating_or_restarting_ui_does_not_affect_core_service_or_labview(self, running_core_service):
        """Closing or crashing UI process/socket has zero impact on Core Service or active LabVIEW sessions."""
        server, kvm_map, (host, port) = running_core_service
        kvm = MockKVMClient(initial_pts=20.0, initial_result="PASS")
        kvm_map["192.168.1.11"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            # LabVIEW opens persistent TCP connection
            with socket.create_connection((host, port), timeout=5.0) as labview_sock:
                # LabVIEW sends a ping
                send_json(labview_sock, {"req_id": "lv-ping-1", "cmd": "ping"})
                lv_ping = recv_json(labview_sock, timeout=2.0)
                assert lv_ping.get("status") == "ok"

                # 1. UI connects and executes queries
                ui_client = CoreServiceClient(host=host, port=port)
                assert ui_client.connect() is True
                ui_status1 = ui_client.get_status()
                assert ui_status1.get("status") in ("running", "ok")

                # 2. UI abruptly terminates (closes socket abruptly / simulated crash)
                ui_client.disconnect()
                time.sleep(0.1)

                # 3. Core Service MUST remain running and healthy
                assert server.is_running is True

                # 4. LabVIEW's connection is intact and can execute check
                send_json(
                    labview_sock,
                    {
                        "req_id": "lv-check-after-ui-crash",
                        "cmd": "check",
                        "device": "2",
                        "station": "BT",
                        "kvm_ip": "192.168.1.11",
                    },
                )
                lv_check = recv_json(labview_sock, timeout=3.0)
                assert lv_check.get("status") == "ok"
                assert lv_check.get("result") == "PASS"

                # 5. UI restarts and reconnects cleanly
                ui_client2 = CoreServiceClient(host=host, port=port)
                assert ui_client2.connect() is True
                ui_status2 = ui_client2.get_status()
                assert ui_status2.get("status") in ("running", "ok")
                ui_client2.disconnect()
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

    def test_ui_manual_diagnostic_check(self, running_core_service):
        """UI manual diagnostic check triggers check and handles busy conflicts gracefully."""
        server, kvm_map, (host, port) = running_core_service
        kvm = MockKVMClient(initial_pts=30.0, initial_result="PASS")
        kvm_map["192.168.1.12"] = kvm

        stop_stream = threading.Event()

        def stream_worker():
            while not stop_stream.is_set():
                time.sleep(0.02)
                kvm.advance(0.033)

        th = threading.Thread(target=stream_worker, daemon=True)
        th.start()

        try:
            client = CoreServiceClient(host=host, port=port)

            # 1. Successful manual check
            res = client.manual_check(
                station="BT",
                device="3",
                kvm_ip="192.168.1.12",
                timeout_sec=2.0,
            )
            assert res.get("status") == "ok"
            assert res.get("result") == "PASS"

            # 2. When device is locked by another client, manual check fails fast with busy
            assert server.lock_manager.try_acquire("3", req_id="other-tester") is True
            res_busy = client.manual_check(
                station="BT",
                device="3",
                kvm_ip="192.168.1.12",
                timeout_sec=1.0,
            )
            assert res_busy.get("status") == "busy"
            assert res_busy.get("error") == "device_busy"

            server.lock_manager.release("3")
            client.disconnect()
        finally:
            stop_stream.set()
            th.join(timeout=1.0)

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
