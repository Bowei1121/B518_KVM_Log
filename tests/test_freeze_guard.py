"""Automated tests for Frame Freeze Guard across all stations (BT, FCT, DFU).

Verifies that Presentation Timestamp (PTS) freeze guard:
1. Detects static timestamps across designated threshold window.
2. Returns {"status": "error", "error": "frame_frozen"} on freeze.
3. Allows live frames with normal timestamp progression to pass.
4. Protects all stations (BT, FCT, DFU) and integrates with TCP JSON server.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from fake_kvm import FakeKVM, make_snapshot
from freeze_guard import FrameFreezeGuard, extract_pts, extract_frame_sequence
from round_frame_consumer import RoundFrameGate, tcp_round_reply
from protocol_helpers import LogSolutionKVM, arm_round, make_registry, wire, numbered, ProbeServer, recv_reply
from tcp_server import TcpJsonServer
from device_lock import DeviceLockManager
from kvm_pool import KVMConnectionPool


ROUTES = {
    "BT-01": {
        "station": "BT",
        "kvm_ip": "192.168.210.11",
        "channels": 4
    },
    "FCT-02": {
        "station": "FCT",
        "kvm_ip": "192.168.210.12",
        "channels": 4
    },
    "BT-03": {
        "station": "BT",
        "kvm_ip": "192.168.210.13",
        "channels": 4
    },
    "BT-10": {
        "station": "BT",
        "kvm_ip": "192.168.210.14",
        "channels": 4
    },
    "FCT-20": {
        "station": "FCT",
        "kvm_ip": "192.168.210.15",
        "channels": 4
    }
}


# ---------------------------------------------------------------------------
# Test Helpers & Mock KVM
# ---------------------------------------------------------------------------

class MockKVMClient(FakeKVM):
    """Mock JetKVM WebRTC client with controllable Presentation Timestamp (PTS)."""

    def __init__(
        self,
        initial_pts: Optional[float] = 100.0,
        initial_sequence: int = 1,
        stream_id: str = "mock-stream-01",
        frame_data: Any = "dummy_frame_data",
    ) -> None:
        super().__init__(frame_data, sequence=initial_sequence, stream_id=stream_id, pts=initial_pts)
        self._pts_delta = 0.033  # ~30fps step

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
    sock.sendall(dumps_request(wire(data)))


def recv_json(sock: socket.socket, timeout: float = 3.0) -> Dict[str, Any]:
    sock.settimeout(timeout)
    return recv_reply(sock)


# ---------------------------------------------------------------------------
# Unit Tests: extract_pts
# ---------------------------------------------------------------------------

class TestExtractPTS:
    def test_extract_pts_from_snapshot(self):
        kvm = FakeKVM("frame", sequence=1, received_monotonic=10.0, stream_id="stream-1", pts=123.456)
        assert extract_pts(kvm) == 123.456

    def test_extract_pts_from_mock_client_latest_frame(self):
        kvm = MockKVMClient(initial_pts=45.67)
        assert extract_pts(kvm) == 45.67

    def test_extract_pts_reads_the_newest_frame(self):
        assert extract_pts(FakeKVM("frame", pts=78.9)) == 78.9

    def test_extract_pts_missing_before_first_frame_or_without_pts(self):
        assert extract_pts(FakeKVM(None)) is None
        assert extract_pts(MockKVMClient(initial_pts=None)) is None


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
        dec1 = gate.observe(None, sequence=1, received_at=0.0, now=0.0, pts=10.0)
        assert dec1.kind != "frozen"

        # Frame at t=0.5, pts=10.0 (static for 0.5s < 1.0s)
        dec2 = gate.observe(None, sequence=2, received_at=0.5, now=0.5, pts=10.0)
        assert dec2.kind == "unknown"

        # Frame at t=1.2, pts=10.0 (static for 1.2s >= 1.0s freeze_threshold)
        dec3 = gate.observe(None, sequence=3, received_at=1.2, now=1.2, pts=10.0)
        assert dec3.kind == "frozen"
        assert dec3.reason == "frame_frozen"
        assert gate.is_frozen is True

        # tcp_round_reply formats to error:frame_frozen
        assert tcp_round_reply(dec3) == "error:frame_frozen\r\n"

    def test_round_frame_gate_recovers_when_pts_resumes_advancing(self):
        gate = RoundFrameGate("DFU-01", freeze_threshold=1.0, require_presentation_time=True)
        gate.observe(None, sequence=1, received_at=0.0, now=0.0, pts=10.0)
        # Static -> frozen
        dec_frozen = gate.observe(None, sequence=2, received_at=1.2, now=1.2, pts=10.0)
        assert dec_frozen.kind == "frozen"

        # Stream recovers with advanced PTS
        dec_alive = gate.observe(None, sequence=3, received_at=1.3, now=1.3, pts=10.1)
        assert dec_alive.kind != "frozen"
        assert gate.is_frozen is False


# ---------------------------------------------------------------------------
# End-to-End Tests: Headless TCP JSON Server with Freeze Guard
# ---------------------------------------------------------------------------

@pytest.fixture
def freeze_rig():
    """Server with one synthetic Log Solution KVM per device IP and a short freeze threshold."""
    from protocol_helpers import LogSolutionKVM

    kvms: Dict[str, Any] = {}

    def client_factory(host: str, password: str = ""):
        return kvms.setdefault(host, LogSolutionKVM())

    server = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=client_factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.3),
    )
    server.start()
    time.sleep(0.05)
    host, port = server.server_address
    try:
        yield server, kvms, (host, port)
    finally:
        server.stop()
        server.wait(timeout=2.0)


def _check_on(address, device, req_id, timeout_sec):
    with socket.create_connection(address, timeout=10.0) as sock:
        send_json(sock, {"protocol_version": "1.0", "req_id": req_id, "device": device,
                         "cmd": "check_finish", "timeout_sec": timeout_sec})
        return recv_json(sock, timeout=10.0)


def _kvm_for(kvms, device):
    from protocol_helpers import LogSolutionKVM

    ip = make_registry().get(device).kvm_ip
    return kvms.setdefault(ip, LogSolutionKVM())


class TestTCPJsonServerFreezeGuard:
    @pytest.mark.parametrize("device", ["DFU-01", "FCT-01", "BT-01"])
    def test_check_frozen_picture_returns_frame_frozen_error(self, freeze_rig, device):
        server, kvms, address = freeze_rig
        kvm = _kvm_for(kvms, device)
        kvm.show("monitoring")
        kvm.freeze()

        res = _check_on(address, device, "req-chk-01", 5)

        assert res["req_id"] == "req-chk-01"
        assert res["status"] == "error"
        assert res["error"] == "frame_frozen"
        assert res["device"] == device

    def test_check_live_picture_is_not_reported_frozen(self, freeze_rig):
        server, kvms, address = freeze_rig
        arm_round(server, _kvm_for(kvms, "DFU-01"), "DFU-01")

        res = _check_on(address, "DFU-01", "req-chk-live", 0)

        assert res["status"] == "ok"
        assert res["data"]["kind"] == "waiting"

    def test_device_lock_released_after_freeze_error(self, freeze_rig):
        server, kvms, address = freeze_rig
        kvm = _kvm_for(kvms, "DFU-01")
        kvm.show("monitoring")
        kvm.freeze()
        assert _check_on(address, "DFU-01", "req-frz", 5)["error"] == "frame_frozen"

        followup = _check_on(address, "DFU-01", "req-after", 0)

        assert followup["error"] != "device_busy"
        assert not server.lock_manager.is_locked("DFU-01")

    def test_multi_device_isolation_frozen_vs_live(self, freeze_rig):
        server, kvms, address = freeze_rig
        frozen = _kvm_for(kvms, "DFU-01")
        frozen.show("monitoring")
        frozen.freeze()
        arm_round(server, _kvm_for(kvms, "DFU-02"), "DFU-02")

        frozen_res = _check_on(address, "DFU-01", "req-f", 5)
        live_res = _check_on(address, "DFU-02", "req-l", 0)

        assert frozen_res["error"] == "frame_frozen"
        assert live_res["status"] == "ok"
        assert live_res["data"]["kind"] == "waiting"

    def test_core_status_reports_frozen_device_only(self, freeze_rig):
        server, kvms, address = freeze_rig
        frozen = _kvm_for(kvms, "DFU-01")
        frozen.show("monitoring")
        frozen.freeze()
        _check_on(address, "DFU-01", "req-frz-st", 5)
        _kvm_for(kvms, "DFU-02").show("monitoring")

        with socket.create_connection(address, timeout=3.0) as sock:
            send_json(sock, {"protocol_version": "1.0", "req_id": "st", "device": "", "cmd": "status"})
            res = recv_json(sock, timeout=3.0)

        frozen_by_device = {d["device"]: d["frozen"] for d in res["data"]["devices"]}
        assert frozen_by_device["DFU-01"] is True
        assert frozen_by_device["DFU-02"] is False


# ---------------------------------------------------------------------------
# Stream reconnect: a new stream restarts the freeze state of its device (#18)
# ---------------------------------------------------------------------------

class _ReconnectingKVM(LogSolutionKVM):
    """Log Solution fake whose stream can be replaced, as after a JetKVM reconnect."""

    def __init__(self):
        super().__init__(state="standby")
        self._stream = "stream-1"

    def reconnect(self):
        with self._lock:
            self._stream = "stream-2"
            self._pts = 1.0  # the new stream's PTS restarts from a low value

    def latest_frame(self):
        return dataclasses.replace(super().latest_frame(), stream_id=self._stream)


class TestStreamReconnectResetsFreezeState:
    def test_reconnected_stream_with_lower_pts_is_not_frozen(self):
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=0.2)
            kvm = MockKVMClient(initial_pts=100.0, stream_id="stream-old")

            async def advance(n):
                for _ in range(n):
                    await asyncio.sleep(0.02)
                    kvm.advance(0.033)

            t = asyncio.create_task(advance(5))
            is_live, err = await guard.verify_liveness(
                kvm, device_id="BT-01", threshold_window=0.2, poll_interval=0.01)
            await t
            assert is_live is True, err

            await asyncio.sleep(0.4)  # old stream stops advancing past the threshold
            kvm.stream_id = "stream-new"  # reconnect: new stream, PTS restarts low
            kvm.frame_presentation_time = 1.0

            t = asyncio.create_task(advance(10))
            is_live, err = await guard.verify_liveness(
                kvm, device_id="BT-01", threshold_window=0.2, poll_interval=0.01)
            await t
            assert is_live is True, err
            assert err is None

        asyncio.run(_run())

    def test_stream_switch_during_window_rebases_the_baseline(self):
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=0.5)
            kvm = MockKVMClient(initial_pts=100.0, stream_id="stream-old")

            async def switch_then_advance():
                await asyncio.sleep(0.05)
                kvm.stream_id = "stream-new"
                kvm.frame_presentation_time = 1.0
                for _ in range(5):
                    await asyncio.sleep(0.02)
                    kvm.advance(0.033)

            t = asyncio.create_task(switch_then_advance())
            is_live, err = await guard.verify_liveness(
                kvm, device_id="BT-01", threshold_window=0.5, poll_interval=0.01)
            await t
            assert is_live is True, err

        asyncio.run(_run())

    def test_same_stream_stalled_pts_still_freezes(self):
        guard = FrameFreezeGuard(freeze_threshold=0.15)
        kvm = MockKVMClient(initial_pts=1.0, stream_id="stream-new")

        is_live, err = guard.verify_liveness_sync(
            kvm, device_id="BT-01", threshold_window=0.15, poll_interval=0.03)

        assert is_live is False
        assert err["error"] == "frame_frozen"

    def test_stream_reset_is_per_device(self):
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=0.15)
            stalled = MockKVMClient(initial_pts=100.0, stream_id="stalled-stream")
            reconnecting = MockKVMClient(initial_pts=100.0, stream_id="old-stream")

            async def advance(kvm, n):
                for _ in range(n):
                    await asyncio.sleep(0.02)
                    kvm.advance(0.033)

            # FCT-02 is frozen on its own stream
            is_live, err = await guard.verify_liveness(
                stalled, device_id="FCT-02", threshold_window=0.15, poll_interval=0.02)
            assert is_live is False and err["error"] == "frame_frozen"

            # BT-01 reconnects onto a new stream with a lower PTS
            t = asyncio.create_task(advance(reconnecting, 5))
            is_live, err = await guard.verify_liveness(
                reconnecting, device_id="BT-01", threshold_window=0.15, poll_interval=0.01)
            await t
            assert is_live is True, err
            reconnecting.stream_id = "new-stream"
            reconnecting.frame_presentation_time = 1.0
            t = asyncio.create_task(advance(reconnecting, 10))
            is_live, err = await guard.verify_liveness(
                reconnecting, device_id="BT-01", threshold_window=0.15, poll_interval=0.01)
            await t
            assert is_live is True, err

            # FCT-02's frozen state is untouched by BT-01's stream change
            is_live, err = await guard.verify_liveness(
                stalled, device_id="FCT-02", threshold_window=0.15, poll_interval=0.02)
            assert is_live is False and err["error"] == "frame_frozen"
            assert guard.get_state("BT-01").is_frozen is False

        asyncio.run(_run())


class _ScriptedKVM(FakeKVM):
    """Returns one (stream_id, pts) reading per latest_frame() call; repeats the last reading."""

    def __init__(self, readings):
        super().__init__()
        self._readings = list(readings)
        self._current = self._readings[0]

    def latest_frame(self):
        if self._readings:
            self._current = self._readings.pop(0)
        stream_id, pts = self._current
        return make_snapshot(None, sequence=1, stream_id=stream_id, pts=pts)


# Old stream runs to PTS 100; the reconnected stream's first frame has no PTS, then restarts low.
RECONNECT_WITHOUT_FIRST_PTS = [
    ("stream-old", 100.0),
    ("stream-new", None),
    ("stream-new", None),
    ("stream-new", 1.0),
    ("stream-new", 1.033),
    ("stream-new", 1.066),
]


class TestReconnectWithoutFirstPTS:
    def test_async_reconnect_with_missing_first_pts_is_not_frozen(self):
        async def _run():
            guard = FrameFreezeGuard(freeze_threshold=1.0)
            kvm = _ScriptedKVM(RECONNECT_WITHOUT_FIRST_PTS)
            is_live, err = await guard.verify_liveness(
                kvm, device_id="BT-01", threshold_window=1.0, poll_interval=0.001)
            assert is_live is True, err

        asyncio.run(_run())

    def test_sync_reconnect_with_missing_first_pts_is_not_frozen(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0)
        kvm = _ScriptedKVM(RECONNECT_WITHOUT_FIRST_PTS)
        is_live, err = guard.verify_liveness_sync(
            kvm, device_id="BT-01", threshold_window=1.0, poll_interval=0.001)
        assert is_live is True, err


class TestCallerSuppliedStreamID:
    def test_caller_stream_id_is_recorded_as_device_baseline(self):
        guard = FrameFreezeGuard(freeze_threshold=0.1)
        kvm = MockKVMClient(initial_pts=1.0, stream_id="stream-new")

        is_live, err = guard.verify_liveness_sync(
            kvm, device_id="BT-01", threshold_window=0.1, poll_interval=0.02, stream_id="stream-new")

        assert is_live is False and err["error"] == "frame_frozen"
        assert guard.get_state("BT-01").stream_id == "stream-new"


class TestStreamReconnectOverTheWire:
    def test_reconnected_stream_does_not_report_frame_frozen(self, freeze_rig):
        server, kvms, address = freeze_rig
        kvm = _ReconnectingKVM()
        kvms[make_registry().get("DFU-01").kvm_ip] = kvm

        assert _check_on(address, "DFU-01", "old-1", 0).get("error") != "frame_frozen"
        time.sleep(0.5)  # last advance on the old stream is older than the 0.3 s threshold
        kvm.reconnect()

        resp = _check_on(address, "DFU-01", "new-1", 0)
        assert resp.get("error") != "frame_frozen", resp


if __name__ == "__main__":
    pytest.main(["-v", __file__])
