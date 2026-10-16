"""check 1.0 must fail closed on every station: no positively observed result means no taken."""
import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import auto_flow
import round_frame_consumer
from round_frame_consumer import TakeDecision
from protocol_helpers import make_registry
from tcp_server import TcpJsonServer


class LiveGuard:
    """Freeze guard stub: stream is always live."""

    async def verify_liveness(self, kvm, device_id=None, threshold_window=None, poll_interval=None, stream_id=None):
        return True, None


class RealishKVM:
    """Mimics JetKVMClient: has an ndarray frame and no result attributes."""

    def __init__(self):
        self.frame = np.zeros((40, 40, 3), dtype=np.uint8)


ALL_STATIONS = ["DFU-01", "FCT-01", "BT-01"]


@pytest.fixture
def server(tmp_path):
    return TcpJsonServer(device_registry=make_registry(), port=0, freeze_guard=LiveGuard(), template_root=tmp_path)


def run_check_cmd(server, req):
    """Drive the check handler directly, the way dispatch would for a device-bound request."""
    req = dict(req)
    req.setdefault("timeout_sec", 0)
    req.setdefault("_kvm_client", RealishKVM())
    req.setdefault("_device_entry", server.device_registry.get(req["device"]))
    server._is_running = True  # handler is driven directly, without the socket loop
    return asyncio.run(server._handle_check(req))


def recording_run_check(calls):
    async def _run_check(kvm, device, *args, **kw):
        calls.append(device)
        return {"ok": True, "rows": [(1, "pass", "SN1"), (2, "pass", "SN2"), (3, "pass", "SN3"), (4, "pass", "SN4")]}

    return _run_check


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_check_never_reads_template_results(server, monkeypatch, device):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", recording_run_check(calls))
    res = run_check_cmd(server, {"device": device})
    assert calls == []
    assert res["data"]["kind"] != "taken"
    assert res["data"]["results"] == []


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_no_frame_yet_is_error_no_result(server, device):
    kvm = RealishKVM()
    kvm.frame = None
    res = run_check_cmd(server, {"device": device, "_kvm_client": kvm})
    assert res["status"] == "error"
    assert res["error"] == "no_frame"


def _round_decision(monkeypatch, decision):
    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", lambda kvm, gate: decision)


@pytest.mark.parametrize(
    "kind,reason",
    [("waiting", "monitoring"), ("paused", "confirmation_required"), ("unknown", "out_of_order_source_frame"),
     ("already_taken", "round_already_taken")],
)
@pytest.mark.parametrize("device", ALL_STATIONS)
def test_single_shot_non_taken_is_not_taken(server, monkeypatch, kind, reason, device):
    _round_decision(monkeypatch, TakeDecision(kind, device, reason=reason))
    res = run_check_cmd(server, {"device": device})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == kind
    assert res["data"]["reason"] == reason
    assert res["data"]["results"] == []


@pytest.mark.parametrize(
    "statuses",
    [("PASS", "PASS", "PASS", "PASS"), ("PASS", "FAIL", "PASS", "PASS"),
     ("PASS", "NOTEST", "PASS", "PASS"), ("TIMEOUT", "PASS", "PASS", "PASS"),
     ("STOPPED", "PASS", "PASS", "PASS")],
)
@pytest.mark.parametrize("device", ALL_STATIONS)
def test_taken_keeps_each_slot_status_and_never_aggregates(server, monkeypatch, statuses, device):
    rows = tuple((i, s) for i, s in enumerate(statuses, 1))
    _round_decision(monkeypatch, TakeDecision("taken", device, capacity=len(rows), results=rows,
                                              reason="reliable_complete"))
    res = run_check_cmd(server, {"device": device})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "taken"
    assert res["data"]["results"] == [{"slot": i, "status": s} for i, s in rows]
    assert "result" not in res["data"]


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_frozen_decision_is_frame_frozen_error(server, monkeypatch, device):
    _round_decision(monkeypatch, TakeDecision("frozen", device, reason="frame_frozen"))
    res = run_check_cmd(server, {"device": device})
    assert res["status"] == "error"
    assert res["error"] == "frame_frozen"


@pytest.mark.parametrize("device", ALL_STATIONS)
def test_inspection_exception_is_internal_error(server, monkeypatch, device):
    def boom(kvm, gate):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", boom)
    res = run_check_cmd(server, {"device": device})
    assert res["status"] == "error"
    assert res["error"] == "internal_error"
