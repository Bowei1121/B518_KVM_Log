"""Automated tests for Frame Freeze Guard across all stations (BT, FCT, DFU).

Verifies that Presentation Timestamp (PTS) freeze guard:
1. Detects static timestamps across designated threshold window.
2. Returns {"status": "error", "error": "frame_frozen"} on freeze.
3. Allows live frames with normal timestamp progression to pass.
4. Protects all stations (BT, FCT, DFU) and integrates with TCP JSON server.
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

from freeze_guard import FrameFreezeGuard, extract_pts, extract_frame_sequence
from round_frame_consumer import RoundFrameGate, tcp_round_reply
from tcp_server import TcpJsonServer
from device_lock import DeviceLockManager
from kvm_pool import KVMConnectionPool


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

class MockKVMClient:
    """Mock JetKVM WebRTC client with controllable Presentation Timestamp (PTS)."""

    def __init__(
        self,
        initial_pts: Optional[float] = 100.0,
        initial_sequence: int = 1,
        stream_id: str = "mock-stream-01",
        frame_data: Any = "dummy_frame_data",
    ) -> None:
        self.frame = frame_data
        self.frame_sequence = initial_sequence
        self.frame_presentation_time = initial_pts
        self.frame_received_monotonic = time.monotonic()
        self.stream_id = stream_id
        self.connected = True
        self._pts_delta = 0.033  # ~30fps step

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

    def freeze(self) -> None:
        """Explicitly freeze stream (timestamps remain static)."""
        pass  # PTS does not change

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False


def send_json(sock: socket.socket, data: Dict[str, Any]) -> None:
    payload = json.dumps(data) + "\n"
    sock.sendall(payload.encode("utf-8"))


def recv_json(sock: socket.socket, timeout: float = 3.0) -> Dict[str, Any]:
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
    raise RuntimeError("No JSON line received")


# ---------------------------------------------------------------------------
# Unit Tests: extract_pts
# ---------------------------------------------------------------------------

class TestExtractPTS:
    def test_extract_pts_from_tuple(self):
        snap = ("frame", 1, 10.0, "stream-1", 123.456)
        assert extract_pts(snap) == 123.456

    def test_extract_pts_from_mock_client_latest_frame(self):
        kvm = MockKVMClient(initial_pts=45.67)
        assert extract_pts(kvm) == 45.67

    def test_extract_pts_from_attributes(self):
        class ObjWithAttr:
            frame_presentation_time = 78.9

        assert extract_pts(ObjWithAttr()) == 78.9

        class ObjWithPts:
            pts = 99.123

        assert extract_pts(ObjWithPts()) == 99.123

        class ObjWithTimestamp:
            timestamp = 55.44

        assert extract_pts(ObjWithTimestamp()) == 55.44

    def test_extract_pts_missing_or_none(self):
        assert extract_pts(None) is None

        class EmptyObj:
            pass

        assert extract_pts(EmptyObj()) is None

        kvm_none = MockKVMClient(initial_pts=None)
        assert extract_pts(kvm_none) is None


# ---------------------------------------------------------------------------
# Unit Tests: FrameFreezeGuard Passive Observation
# ---------------------------------------------------------------------------

class TestFrameFreezeGuardObservation:
    def test_initial_frame_records_without_error(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        res = guard.observe("dev-1", pts=10.0, now=1.0)
        assert res["is_frozen"] is False
        assert res["status"] == "initial"

    def test_incrementing_pts_is_live(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        guard.observe("dev-1", pts=10.0, now=1.0)
        res1 = guard.observe("dev-1", pts=10.033, now=1.033)
        assert res1["is_frozen"] is False
        assert res1["status"] == "ok"
        assert res1["reason"] == "incrementing"

        res2 = guard.observe("dev-1", pts=10.066, now=1.066)
        assert res2["is_frozen"] is False
        assert res2["status"] == "ok"

    def test_static_pts_warns_before_threshold(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        guard.observe("dev-1", pts=10.0, now=1.0)
        # Static PTS after 0.5s (< 1.0s threshold)
        res = guard.observe("dev-1", pts=10.0, now=1.5)
        assert res["is_frozen"] is False
        assert res["status"] == "warning"
        assert res["reason"] == "timestamp_not_advanced"
        assert res["static_seconds"] == 0.5

    def test_static_pts_fails_when_threshold_reached(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        guard.observe("dev-1", pts=10.0, now=1.0)
        # Static PTS at 1.0s and 1.5s (duration = 1.5s >= 1.0s)
        guard.observe("dev-1", pts=10.0, now=1.5)
        res = guard.observe("dev-1", pts=10.0, now=2.1)
        assert res["is_frozen"] is True
        assert res["status"] == "error"
        assert res["error"] == "frame_frozen"
        assert res["static_seconds"] >= 1.0

    def test_missing_pts_fails_fast_when_required(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0, require_pts=True)
        res = guard.observe("dev-1", pts=None, now=1.0)
        assert res["is_frozen"] is True
        assert res["status"] == "error"
        assert res["error"] == "frame_frozen"

    def test_reset_clears_device_state(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        guard.observe("dev-1", pts=10.0, now=1.0)
        guard.observe("dev-1", pts=10.0, now=2.5)  # frozen
        assert guard.get_state("dev-1").is_frozen is True

        guard.reset("dev-1")
        assert guard.get_state("dev-1").last_pts is None
        assert guard.get_state("dev-1").is_frozen is False


# ---------------------------------------------------------------------------
# Unit Tests: Active Liveness Verification (Async & Sync)
# ---------------------------------------------------------------------------

class TestActiveLivenessVerification:
    def test_static_kvm_fails_fast_with_frame_frozen(self):
        """Mock KVM with static timestamp must return {"status": "error", "error": "frame_frozen"}."""
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=0.2)
            kvm = MockKVMClient(initial_pts=100.0)

            is_live, err = await guard.verify_liveness(
                kvm,
                device_id="BT-01",
                threshold_window=0.2,
                poll_interval=0.03,
            )

            assert is_live is False
            assert err is not None
            assert err["status"] == "error"
            assert err["error"] == "frame_frozen"
            assert err["device"] == "BT-01"

        asyncio.run(_run())

    def test_live_kvm_advancing_pts_passes_normally(self):
        """Mock KVM with advancing timestamp passes verification immediately."""
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=0.5)
            kvm = MockKVMClient(initial_pts=100.0)

            # Background task that simulates video stream frames arriving
            async def stream_frames():
                for _ in range(5):
                    await asyncio.sleep(0.04)
                    kvm.advance(0.033)

            stream_task = asyncio.create_task(stream_frames())
            try:
                is_live, err = await guard.verify_liveness(
                    kvm,
                    device_id="FCT-01",
                    threshold_window=0.5,
                    poll_interval=0.02,
                )
                assert is_live is True
                assert err is None
            finally:
                await stream_task

        asyncio.run(_run())

    def test_sync_static_kvm_fails_with_frame_frozen(self):
        guard = FrameFreezeGuard(freeze_threshold=0.15)
        kvm = MockKVMClient(initial_pts=50.0)

        is_live, err = guard.verify_liveness_sync(
            kvm,
            device_id="DFU-01",
            threshold_window=0.15,
            poll_interval=0.03,
        )

        assert is_live is False
        assert err is not None
        assert err["status"] == "error"
        assert err["error"] == "frame_frozen"

    def test_sync_live_kvm_passes_when_already_advanced(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        kvm = MockKVMClient(initial_pts=50.0)
        guard.observe("dev-sync", pts=50.0)

        # Advance PTS before verification
        kvm.advance(0.1)
        is_live, err = guard.verify_liveness_sync(kvm, device_id="dev-sync")
        assert is_live is True
        assert err is None


# ---------------------------------------------------------------------------
# Station-Specific Tests: DFU RoundFrameGate Freeze Detection
# ---------------------------------------------------------------------------

class TestDFURoundFrameGateFreeze:
    def test_round_frame_gate_detects_frozen_pts(self):
        """RoundFrameGate must fail with reason='frame_frozen' when PTS remains static."""
        gate = RoundFrameGate("DFU-01", freeze_threshold=1.0, require_presentation_time=True)

        # First frame at t=0, pts=10.0
        dec1 = gate.observe(None, sequence=1, received_at=0.0, now=0.0, presentation_time=10.0)
        assert dec1.kind != "frozen"

        # Frame at t=0.5, pts=10.0 (static for 0.5s < 1.0s)
        dec2 = gate.observe(None, sequence=2, received_at=0.5, now=0.5, presentation_time=10.0)
        assert dec2.kind == "unknown"

        # Frame at t=1.2, pts=10.0 (static for 1.2s >= 1.0s freeze_threshold)
        dec3 = gate.observe(None, sequence=3, received_at=1.2, now=1.2, presentation_time=10.0)
        assert dec3.kind == "frozen"
        assert dec3.reason == "frame_frozen"
        assert gate.is_frozen is True

        # tcp_round_reply formats to error:frame_frozen
        assert tcp_round_reply(dec3) == "error:frame_frozen\r\n"

    def test_round_frame_gate_recovers_when_pts_resumes_advancing(self):
        gate = RoundFrameGate("DFU-01", freeze_threshold=1.0, require_presentation_time=True)
        gate.observe(None, sequence=1, received_at=0.0, now=0.0, presentation_time=10.0)
        # Static -> frozen
        dec_frozen = gate.observe(None, sequence=2, received_at=1.2, now=1.2, presentation_time=10.0)
        assert dec_frozen.kind == "frozen"

        # Stream recovers with advanced PTS
        dec_alive = gate.observe(None, sequence=3, received_at=1.3, now=1.3, presentation_time=10.1)
        assert dec_alive.kind != "frozen"
        assert gate.is_frozen is False


# ---------------------------------------------------------------------------
# End-to-End Tests: Headless TCP JSON Server with Freeze Guard
# ---------------------------------------------------------------------------

@pytest.fixture
def running_server_with_kvm():
    """Start TcpJsonServer with custom mock KVM pool."""
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


class TestTCPJsonServerFreezeGuard:
    def test_check_static_kvm_returns_frame_frozen_error(self, running_server_with_kvm):
        """When station check runs against static KVM, returns {"status": "error", "error": "frame_frozen"}."""
        server, kvm_map, (host, port) = running_server_with_kvm
        # Pre-seed mock KVM
        kvm_client = MockKVMClient(initial_pts=10.0)
        kvm_map["192.168.1.100"] = kvm_client

        with socket.create_connection((host, port), timeout=3.0) as sock:
            send_json(
                sock,
                {
                    "req_id": "req-chk-01",
                    "cmd": "check",
                    "station": "BT",
                    "device": "1",
                    "kvm_ip": "192.168.1.100",
                    "freeze_threshold": 0.15,
                },
            )
            res = recv_json(sock, timeout=3.0)

            assert res["req_id"] == "req-chk-01"
            assert res["status"] == "error"
            assert res["error"] == "frame_frozen"
            assert res["device"] == "1"
            assert "Frame presentation timestamp" in res.get("message", "")

    def test_check_live_advancing_kvm_passes_normally(self, running_server_with_kvm):
        """When station check runs against live advancing KVM, passes with status ok."""
        server, kvm_map, (host, port) = running_server_with_kvm
        kvm_client = MockKVMClient(initial_pts=20.0)
        kvm_map["192.168.1.101"] = kvm_client

        # Background thread that simulates advancing frames
        stop_event = threading.Event()

        def stream_worker():
            while not stop_event.is_set():
                time.sleep(0.03)
                kvm_client.advance(0.033)

        thread = threading.Thread(target=stream_worker, daemon=True)
        thread.start()

        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                send_json(
                    sock,
                    {
                        "req_id": "req-chk-02",
                        "cmd": "check",
                        "station": "FCT",
                        "device": "2",
                        "kvm_ip": "192.168.1.101",
                        "freeze_threshold": 0.3,
                    },
                )
                res = recv_json(sock, timeout=3.0)

                assert res["req_id"] == "req-chk-02"
                assert res["status"] == "ok"
                assert res["device"] == "2"
                assert res.get("verified") is True
        finally:
            stop_event.set()
            thread.join(timeout=1.0)

    def test_device_lock_released_after_freeze_error(self, running_server_with_kvm):
        """Ensures that per-device lock is cleanly released when frame_frozen error occurs."""
        server, kvm_map, (host, port) = running_server_with_kvm
        kvm_map["192.168.1.102"] = MockKVMClient(initial_pts=30.0)

        with socket.create_connection((host, port), timeout=3.0) as sock:
            # 1. Trigger freeze error
            send_json(
                sock,
                {
                    "req_id": "req-chk-03a",
                    "cmd": "check",
                    "station": "BT",
                    "device": "3",
                    "kvm_ip": "192.168.1.102",
                    "freeze_threshold": 0.1,
                },
            )
            res1 = recv_json(sock)
            assert res1["status"] == "error"
            assert res1["error"] == "frame_frozen"

            # 2. Immediately send another command for same device 3.
            # Must NOT be blocked by device_busy!
            send_json(
                sock,
                {
                    "req_id": "req-chk-03b",
                    "cmd": "ping",
                    "device": "3",
                },
            )
            res2 = recv_json(sock)
            assert res2["status"] == "ok"
            assert res2["data"] == "pong"

    def test_multi_station_isolation_frozen_vs_live(self, running_server_with_kvm):
        """Station BT (frozen) fails while Station FCT (live) succeeds concurrently."""
        server, kvm_map, (host, port) = running_server_with_kvm
        frozen_kvm = MockKVMClient(initial_pts=40.0)
        live_kvm = MockKVMClient(initial_pts=50.0)
        kvm_map["192.168.1.104"] = frozen_kvm
        kvm_map["192.168.1.105"] = live_kvm

        # Stream live_kvm in background thread
        stop_stream = threading.Event()

        def live_streamer():
            while not stop_stream.is_set():
                time.sleep(0.02)
                live_kvm.advance(0.033)

        stream_th = threading.Thread(target=live_streamer, daemon=True)
        stream_th.start()

        try:
            with socket.create_connection((host, port), timeout=3.0) as sock_frozen, \
                 socket.create_connection((host, port), timeout=3.0) as sock_live:

                # Send to frozen station
                send_json(
                    sock_frozen,
                    {
                        "req_id": "req-frozen",
                        "cmd": "check",
                        "station": "BT",
                        "device": "10",
                        "kvm_ip": "192.168.1.104",
                        "freeze_threshold": 0.15,
                    },
                )
                # Send to live station
                send_json(
                    sock_live,
                    {
                        "req_id": "req-live",
                        "cmd": "check",
                        "station": "FCT",
                        "device": "20",
                        "kvm_ip": "192.168.1.105",
                        "freeze_threshold": 0.3,
                    },
                )

                res_frozen = recv_json(sock_frozen)
                res_live = recv_json(sock_live)

                assert res_frozen["status"] == "error"
                assert res_frozen["error"] == "frame_frozen"
                assert res_frozen["device"] == "10"

                assert res_live["status"] == "ok"
                assert res_live["device"] == "20"
        finally:
            stop_stream.set()
            stream_th.join(timeout=1.0)

    def test_status_command_reports_freeze_guard_stats(self, running_server_with_kvm):
        server, _, (host, port) = running_server_with_kvm
        with socket.create_connection((host, port), timeout=2.0) as sock:
            send_json(sock, {"req_id": "stat-1", "cmd": "status"})
            res = recv_json(sock)
            assert res["status"] == "ok"
            status_data = res["data"]
            assert "freeze_guard" in status_data
            fg_info = status_data["freeze_guard"]
            assert "freeze_threshold" in fg_info
            assert "tracked_devices_count" in fg_info


class TestStationVisionChecksFreeze:
    """Test station-level visual check integration (BT, FCT) with FrameFreezeGuard."""

    def test_bt_run_check_detects_frame_frozen_on_static_pts(self, tmp_path):
        from auto_flow import run_check
        import numpy as np

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        kvm = MockKVMClient(initial_pts=100.0, frame_data=frame)
        guard = FrameFreezeGuard(freeze_threshold=0.1)

        result = asyncio.run(run_check(
            kvm,
            device="BT",
            template_root=tmp_path,
            freeze_guard=guard,
            inspection_window=0.1,
        ))

        assert result["ok"] is False
        assert result["status"] == "error"
        assert result["error"] == "frame_frozen"
        assert "Frame presentation timestamp" in result["message"]

    def test_fct_run_check_detects_frame_frozen_on_static_pts(self, tmp_path):
        from auto_flow import run_check
        import numpy as np

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        kvm = MockKVMClient(initial_pts=200.0, frame_data=frame)
        guard = FrameFreezeGuard(freeze_threshold=0.1)

        result = asyncio.run(run_check(
            kvm,
            device="FCT",
            template_root=tmp_path,
            freeze_guard=guard,
            inspection_window=0.1,
        ))

        assert result["ok"] is False
        assert result["status"] == "error"
        assert result["error"] == "frame_frozen"

    def test_bt_run_check_passes_freeze_guard_when_pts_advances(self, tmp_path):
        from auto_flow import run_check
        from template_catalog import TemplateCatalog
        import numpy as np

        catalog = TemplateCatalog(tmp_path)
        rng = np.random.RandomState(42)
        frame = rng.randint(0, 255, (180, 260, 3), dtype=np.uint8)
        catalog.save_crop(frame, (15, 20, 125, 140), "BT", "window")
        catalog.save_crop(frame, (40, 50, 75, 80), "BT", "testing")

        kvm = MockKVMClient(initial_pts=10.0, frame_data=frame)
        guard = FrameFreezeGuard(freeze_threshold=0.5)

        # Background task that advances PTS
        async def stream():
            for _ in range(5):
                await asyncio.sleep(0.02)
                kvm.advance(0.033)

        async def check():
            t = asyncio.create_task(stream())
            try:
                return await run_check(
                    kvm,
                    device="BT",
                    template_root=catalog.root,
                    freeze_guard=guard,
                    inspection_window=0.5,
                )
            finally:
                await t

        result = asyncio.run(check())
        # Live frame passed freeze guard; then matched window and testing templates!
        assert result.get("error") != "frame_frozen"
        assert result["ok"] is True
        assert result["testing"] is True


if __name__ == "__main__":
    pytest.main(["-v", __file__])
