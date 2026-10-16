"""check 1.0 must fail closed: no positively observed result means no taken."""
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


DEVICE_BY_STATION = {"FCT": "FCT-01", "BT": "BT-01", "DFU": "DFU-01"}


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


def fake_run_check(result, calls=None):
    async def _run_check(kvm, device, template_root=None, freeze_guard=None, **kw):
        if calls is not None:
            calls.append({"device": device, "template_root": template_root})
        if isinstance(result, Exception):
            raise result
        return result

    return _run_check


@pytest.mark.parametrize("station", ["FCT", "BT"])
def test_empty_rows_is_not_taken(server, monkeypatch, station):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "testing": False, "rows": []}))
    res = run_check_cmd(server, {"device": DEVICE_BY_STATION[station]})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "unknown"
    assert res["data"]["results"] == []


def test_window_not_found_is_unknown_not_taken(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False, "window": 0.12}))
    res = run_check_cmd(server, {"device": "BT-01"})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "unknown"
    assert res["data"]["results"] == []


def test_window_not_found_with_timeout_times_out_with_last_state(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False}))
    res = run_check_cmd(server, {"device": "BT-01", "timeout_sec": 0.2})
    assert res["status"] == "timeout"
    assert res["error"] == "timeout"
    assert res["data"]["kind"] == "unknown"


def test_run_check_exception_is_internal_error_not_taken(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check(RuntimeError("boom")))
    res = run_check_cmd(server, {"device": "BT-01"})
    assert res["status"] == "error"
    assert res["error"] == "internal_error"
    assert res["data"]["results"] == []


def test_template_rows_map_to_slot_results(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass", "SN1"), (2, "fail", "SN2")]}))
    res = run_check_cmd(server, {"device": "BT-01"})
    assert res["status"] == "error"  # capacity 2 != 4 configured channels
    assert res["error"] == "capacity_mismatch"
    assert res["data"]["results"] == []


def test_template_rows_matching_channels_are_taken_per_slot(server, monkeypatch):
    # Same shape as auto_flow.run_check rows: (index, result, sn).
    rows = [(1, "pass", "SN1"), (2, "fail", "SN2"), (3, "pass", "SN3"), (4, "pass", "SN4")]
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": rows}))
    res = run_check_cmd(server, {"device": "BT-01"})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "taken"
    assert res["data"]["results"] == [
        {"slot": 1, "status": "PASS"}, {"slot": 2, "status": "FAIL"},
        {"slot": 3, "status": "PASS"}, {"slot": 4, "status": "PASS"},
    ]
    assert "result" not in res["data"]


def test_no_template_root_uses_default_catalog_root(monkeypatch):
    from template_catalog import TemplateCatalog

    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": []}, calls))
    server = TcpJsonServer(device_registry=make_registry(), port=0, freeze_guard=LiveGuard())
    run_check_cmd(server, {"device": "BT-01"})
    assert calls == [{"device": "BT", "template_root": TemplateCatalog.default_root()}]


def test_request_template_root_overrides_server_root(server, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False}, calls))
    other = str(tmp_path / "other")
    run_check_cmd(server, {"device": "BT-01", "template_root": other})
    assert calls[0]["template_root"] == other


def test_no_frame_yet_is_error_no_result(server, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass", "SN1")]}, calls))
    kvm = RealishKVM()
    kvm.frame = None
    res = run_check_cmd(server, {"device": "BT-01", "_kvm_client": kvm})
    assert calls == []
    assert res["status"] == "error"
    assert res["error"] == "no_frame"


def _dfu_decision(monkeypatch, decision):
    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", lambda kvm, gate: decision)


@pytest.mark.parametrize(
    "kind,reason",
    [("waiting", "monitoring"), ("paused", "confirmation_required"), ("unknown", "out_of_order_source_frame"),
     ("already_taken", "round_already_taken")],
)
def test_dfu_single_shot_non_taken_is_not_taken(server, monkeypatch, kind, reason):
    _dfu_decision(monkeypatch, TakeDecision(kind, "DFU-01", reason=reason))
    res = run_check_cmd(server, {"device": "DFU-01"})
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
def test_dfu_taken_keeps_each_slot_status_and_never_aggregates(server, monkeypatch, statuses):
    rows = tuple((i, s) for i, s in enumerate(statuses, 1))
    _dfu_decision(monkeypatch, TakeDecision("taken", "DFU-01", capacity=len(rows), results=rows,
                                            reason="reliable_complete"))
    res = run_check_cmd(server, {"device": "DFU-01"})
    assert res["status"] == "ok"
    assert res["data"]["kind"] == "taken"
    assert res["data"]["results"] == [{"slot": i, "status": s} for i, s in rows]
    assert "result" not in res["data"]


def test_dfu_frozen_decision_is_frame_frozen_error(server, monkeypatch):
    _dfu_decision(monkeypatch, TakeDecision("frozen", "DFU-01", reason="frame_frozen"))
    res = run_check_cmd(server, {"device": "DFU-01"})
    assert res["status"] == "error"
    assert res["error"] == "frame_frozen"


def test_dfu_inspection_exception_is_internal_error(server, monkeypatch):
    def boom(kvm, gate):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", boom)
    res = run_check_cmd(server, {"device": "DFU-01"})
    assert res["status"] == "error"
    assert res["error"] == "internal_error"
