"""Every frame-decoding path records its reading once (issue #94), and core_status never reads a frame.

Each path is driven on its own, so a path that stops recording fails its own test.
"""

import asyncio
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_protocol import dumps_request  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
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


@pytest.fixture
def rig():
    registry = make_registry()
    kvms = {}
    by_ip = {
        registry.get(device).kvm_ip: CountingKVM(state="monitoring", statuses=["TESTING", "PASS"])
        for device in ("DFU-01", "FCT-01", "BT-01")
    }

    def client_factory(host, password=""):
        return by_ip[host]

    srv = TcpJsonServer(
        device_registry=registry,
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=client_factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.05),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, registry, kvms, by_ip
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
    srv, registry, kvms, by_ip = rig
    _send(srv, "DFU-01", "inspect", timeout_sec=0)
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    reads_before = kvm.reads

    res = _send(srv, "", "core_status")

    assert kvm.reads == reads_before
    body = res.get("data", res)
    row = next(row for row in body["stations"] if row["device"] == "DFU-01")
    assert row["readable_ratio"]["samples"] >= 1
    for key in ("frame_size", "picture_scale", "cell_px", "last_reading", "readable_ratio"):
        assert key in row


def test_inspect_records_every_frame_it_reads(rig):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    before = _samples(srv, "DFU-01")
    reads_before = kvm.reads

    _send(srv, "DFU-01", "inspect", timeout_sec=0)

    # Each inspect reading decodes one frame: records and latest_frame() reads move together.
    assert _samples(srv, "DFU-01") - before == kvm.reads - reads_before
    assert _samples(srv, "DFU-01") > before


def test_monitor_refusal_records_once(rig):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    stats = srv._slot("DFU-01").stats

    assert srv._monitor_refusal(kvm, stats) is None
    assert stats.report()["readable_ratio"]["samples"] == 1


def test_log_solution_refusal_records_once(rig):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")

    srv._log_solution_refusal(kvm, registry.get("DFU-01"))

    assert _samples(srv, "DFU-01") == 1


def test_heartbeat_probe_decodes_are_recorded(rig):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    ctx = CommandContext(entry=registry.get("DFU-01"), kvm=kvm)

    asyncio.run(srv._heartbeat_refusal(ctx))

    assert _samples(srv, "DFU-01") >= 1


def test_monitor_start_first_read_is_recorded(rig, tmp_path):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    slot = srv._slot("DFU-01")

    # Monitoring on screen refuses at once, after the first read only.
    with pytest.raises(Exception):
        asyncio.run(run_monitor_start(kvm, slot.gate, diagnostics_key="DFU-01", template_root=tmp_path))

    assert slot.stats.report()["readable_ratio"]["samples"] == 1


def test_dock_recovery_read_is_recorded(rig, tmp_path):
    srv, registry, kvms, by_ip = rig
    kvm = _kvm_of(by_ip, registry, "DFU-01")
    stats = srv._slot("DFU-01").stats

    # No Dock template under tmp_path: the read happens, then the click stops with hid_failed.
    with pytest.raises(Exception):
        asyncio.run(bring_log_solution_forward(kvm, diagnostics_key="DFU-01", template_root=tmp_path, stats=stats))

    assert stats.report()["readable_ratio"]["samples"] == 1


def test_heartbeat_stopped_frame_is_recorded_once_per_frame():
    from log_solution_heartbeat import HEARTBEAT_STOPPED, HeartbeatTracker
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

    assert decision.kind == "heartbeat_stopped" or decision.reason == HEARTBEAT_STOPPED
    assert stats.report(now=now)["readable_ratio"]["samples"] == 3
