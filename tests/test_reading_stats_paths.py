"""Every frame-decoding path records its reading once (issue #94), and core_status never reads a frame.

Each path is driven on its own, so a path that stops recording fails its own test. The wiring tests check
that the real call site hands over the device's own slot stats.
"""

import asyncio
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import monitor_flow  # noqa: E402
from core_protocol import dumps_request  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from hid_session import FlowError  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from monitor_flow import bring_log_solution_forward, run_monitor_start  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry, recv_reply  # noqa: E402
from tcp_server import CommandContext, TcpJsonServer  # noqa: E402


class CountingKVM(LogSolutionKVM):
    """A Log Solution KVM that counts latest_frame() calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = 0

    def latest_frame(self):
        self.reads += 1
        return super().latest_frame()


def _pool_by_ip(kvms_by_ip):
    return KVMConnectionPool(client_factory=lambda host, password="": kvms_by_ip[host])


@pytest.fixture
def rig():
    registry = make_registry()
    by_ip = {
        registry.get(device).kvm_ip: CountingKVM(state="monitoring", statuses=["TESTING", "PASS"])
        for device in ("DFU-01", "FCT-01", "BT-01")
    }
    srv = TcpJsonServer(
        device_registry=registry,
        host="127.0.0.1",
        port=0,
        kvm_pool=_pool_by_ip(by_ip),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.05),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, registry, by_ip
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, device, cmd, **extra):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": cmd, "cmd": cmd, "device": device, **extra}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def _kvm_of(by_ip, registry, device):
    return by_ip[registry.get(device).kvm_ip]


def _samples(srv, device):
    return srv._slot(device).stats.report()["readable_ratio"]["samples"]


def test_core_status_reads_no_frame_and_reports_recorded_fields(rig):
    srv, registry, by_ip = rig
    _send(srv, "DFU-01", "inspect", timeout_sec=0)
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    reads_before = kvm.reads

    res = _send(srv, "", "core_status")

    assert kvm.reads == reads_before
    row = next(row for row in res["data"]["stations"] if row["device"] == "DFU-01")
    frame = kvm.latest_frame().image
    assert row["is_connected"] is True
    assert row["frame_size"] == [frame.shape[1], frame.shape[0]]
    assert row["readable_ratio"]["samples"] >= 1
    for key in ("frame_size", "picture_scale", "cell_px", "last_reading", "readable_ratio"):
        assert key in row


def test_inspect_records_every_frame_it_reads(rig):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    before = _samples(srv, "DFU-01")
    reads_before = kvm.reads

    _send(srv, "DFU-01", "inspect", timeout_sec=0)

    # Each inspect reading decodes one frame: records and latest_frame() reads move together.
    assert _samples(srv, "DFU-01") - before == kvm.reads - reads_before
    assert _samples(srv, "DFU-01") > before


def test_monitor_refusal_records_once(rig):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    stats = srv._slot("DFU-01").stats

    assert srv._monitor_refusal(kvm, stats) is None
    assert stats.report()["readable_ratio"]["samples"] == 1


def test_log_solution_refusal_records_once(rig):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")

    srv._log_solution_refusal(kvm, registry.get("DFU-01"))

    assert _samples(srv, "DFU-01") == 1


def test_heartbeat_probe_decodes_are_recorded(rig):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    ctx = CommandContext(entry=registry.get("DFU-01"), kvm=kvm)

    asyncio.run(srv._heartbeat_refusal(ctx))

    assert _samples(srv, "DFU-01") >= 1


def test_monitor_start_first_read_is_recorded(rig, tmp_path):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    slot = srv._slot("DFU-01")

    # Monitoring on screen refuses at once, after the first read only.
    with pytest.raises(FlowError) as refused:
        asyncio.run(run_monitor_start(kvm, slot.gate, diagnostics_key="DFU-01", template_root=tmp_path))

    assert refused.value.code == "log_solution_busy"
    assert slot.stats.report()["readable_ratio"]["samples"] == 1


def test_dock_recovery_read_is_recorded(rig, tmp_path):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    stats = srv._slot("DFU-01").stats

    # No Dock template under tmp_path: the read happens, then the click stops with hid_failed.
    with pytest.raises(FlowError) as failed:
        asyncio.run(bring_log_solution_forward(kvm, diagnostics_key="DFU-01", template_root=tmp_path, stats=stats))

    assert failed.value.code == "hid_failed"
    assert stats.report()["readable_ratio"]["samples"] == 1


def test_wait_for_reliable_reading_records_its_read(rig):
    srv, registry, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    stats = srv._slot("DFU-01").stats

    observation = asyncio.run(monitor_flow._wait_for_reliable_reading(kvm, 0, stats))

    assert observation is not None and observation.reliable
    assert stats.report()["readable_ratio"]["samples"] == 1


def test_start_test_passes_the_device_slot_stats_to_the_monitor_refusal(rig, monkeypatch):
    srv, registry, by_ip = rig
    seen = []

    def spy(kvm, stats=None):
        seen.append(stats)
        return ("monitor_not_ready", "stopped here: no HID")

    monkeypatch.setattr(srv, "_monitor_refusal", spy)
    _send(srv, "DFU-01", "start_test")

    assert seen and seen[0] is srv._slot("DFU-01").stats


def test_check_dock_recovery_passes_the_device_slot_stats(tmp_path, monkeypatch):
    """A covered Log Solution window makes check_start (not inspect) click the Dock: the click must carry the stats."""
    registry = make_registry()
    kvm = CountingKVM(state="monitoring", statuses=["TESTING", "PASS"])
    kvm.cover()
    srv = TcpJsonServer(
        device_registry=registry,
        host="127.0.0.1",
        port=0,
        kvm_pool=_pool_by_ip({registry.get("DFU-01").kvm_ip: kvm}),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        template_root=tmp_path,
        visibility_lost_sec=0.2,
        dock_recheck_sec=0.1,
    )
    seen = []

    async def spy(kvm_arg, diagnostics_key, template_root=None, threshold=0.8, stats=None):
        seen.append(stats)
        return False

    monkeypatch.setattr(monitor_flow, "bring_log_solution_forward", spy)
    srv.start()
    time.sleep(0.05)
    try:
        _send(srv, "DFU-01", "check_start", timeout_sec=2)
    finally:
        srv.stop()
        srv.wait(timeout=2.0)

    assert seen and seen[0] is srv._slot("DFU-01").stats


def test_heartbeat_stopped_frame_is_recorded_once_per_frame():
    from log_solution_heartbeat import HeartbeatTracker
    from protocol_helpers import render_log_solution
    from reading_stats import ReadingStats
    from round_frame_consumer import RoundFrameGate

    stats = ReadingStats()
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, heartbeat=HeartbeatTracker(), stats=stats)
    frame = render_log_solution("monitoring", ["TESTING", "PASS"], scale=2, heartbeat=5)
    decision = None
    now = 100.0
    for sequence in range(1, 4):
        decision = gate.observe(frame, sequence, now, now=now, stream_id="s")
        now += 1.0

    assert decision.kind == "heartbeat_stopped"
    assert stats.report(now=now)["readable_ratio"]["samples"] == 3
