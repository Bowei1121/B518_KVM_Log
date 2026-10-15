"""check must fail closed: no positively observed result → never result=PASS."""
import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import auto_flow
import round_frame_consumer
from round_frame_consumer import TakeDecision
from protocol_helpers import make_registry, wire, numbered
from tcp_server import TcpJsonServer
from template_catalog import TemplateCatalog


class LiveGuard:
    """Freeze guard stub: stream is always live."""

    async def verify_liveness(self, kvm, device_id=None, threshold_window=None, poll_interval=None):
        return True, None


class RealishKVM:
    """Mimics JetKVMClient: has an ndarray frame, no mock result attributes."""

    def __init__(self):
        self.frame = np.zeros((40, 40, 3), dtype=np.uint8)


def run_check_cmd(server, req):
    req = dict(req)
    req.setdefault("_kvm_client", RealishKVM())
    server._is_running = True  # handler is driven directly, without the socket loop
    return asyncio.run(server._handle_check(req))


@pytest.fixture
def server(tmp_path):
    return TcpJsonServer(device_registry=make_registry(), port=0, freeze_guard=LiveGuard(), template_root=tmp_path)


def fake_run_check(result, calls=None):
    async def _run_check(kvm, device, template_root=None, freeze_guard=None, **kw):
        if calls is not None:
            calls.append({"device": device, "template_root": template_root})
        if isinstance(result, Exception):
            raise result
        return result

    return _run_check


@pytest.mark.parametrize("station", ["FCT", "BT"])
def test_empty_rows_is_not_pass(server, monkeypatch, station):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "testing": False, "rows": []}))
    res = run_check_cmd(server, {"station": station, "device": "DFU-01"})
    assert res["result"] != "PASS"
    assert res["status"] == "error"
    assert res["error"] == "no_result"


def test_window_not_found_without_timeout_is_not_pass(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False, "window": 0.12}))
    res = run_check_cmd(server, {"station": "FCT", "device": "DFU-01"})
    assert res["result"] == "NONE"
    assert res["error"] == "no_result"
    assert res["check_result"]["window"] == 0.12


def test_window_not_found_with_timeout_times_out(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False}))
    res = run_check_cmd(server, {"station": "BT", "device": "DFU-01", "timeout_sec": 0.2, "poll_interval": 0.05})
    assert res["status"] == "timeout"
    assert res["result"] == "NONE"


def test_run_check_exception_is_error_not_pass(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check(RuntimeError("boom")))
    res = run_check_cmd(server, {"station": "FCT", "device": "DFU-01"})
    assert res["status"] == "error"
    assert res["error"] == "check_failed"
    assert "result" not in res or res["result"] != "PASS"


def test_rows_all_pass_is_pass_any_fail_is_fail(server, monkeypatch):
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass"), (2, "pass")]}))
    assert run_check_cmd(server, {"station": "FCT", "device": "DFU-01"})["result"] == "PASS"
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass"), (2, "fail")]}))
    assert run_check_cmd(server, {"station": "FCT", "device": "DFU-01"})["result"] == "FAIL"


def test_no_template_root_uses_default_catalog_root(monkeypatch):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass")]}, calls))
    server = TcpJsonServer(device_registry=make_registry(), port=0, freeze_guard=LiveGuard())
    res = run_check_cmd(server, {"station": "FCT", "device": "DFU-01"})
    assert res["result"] == "PASS"
    assert calls == [{"device": "FCT", "template_root": TemplateCatalog.default_root()}]


def test_request_template_root_overrides_server_root(server, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": False}, calls))
    other = str(tmp_path / "other")
    run_check_cmd(server, {"station": "BT", "device": "DFU-01", "template_root": other})
    assert calls[0]["template_root"] == other


def test_no_frame_yet_is_no_result(server, monkeypatch):
    calls = []
    monkeypatch.setattr(auto_flow, "run_check", fake_run_check({"ok": True, "rows": [(1, "pass")]}, calls))
    kvm = RealishKVM()
    kvm.frame = None
    res = run_check_cmd(server, {"station": "FCT", "device": "DFU-01", "_kvm_client": kvm})
    assert calls == []
    assert res["error"] == "no_result"


def _dfu_decision(monkeypatch, decision):
    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", lambda kvm, gate: decision)


@pytest.mark.parametrize(
    "kind,reason",
    [("waiting", "monitoring"), ("paused", "confirmation_required"), ("unknown", "no_frame"),
     ("already_taken", "round_already_taken")],
)
def test_dfu_single_shot_non_taken_is_not_pass(server, monkeypatch, kind, reason):
    _dfu_decision(monkeypatch, TakeDecision(kind, "DFU:1", reason=reason))
    res = run_check_cmd(server, {"station": "DFU", "device": "DFU-01"})
    assert res["result"] != "PASS"
    assert res["error"] == "no_result"
    assert res["kind"] == kind
    assert res["reason"] == reason


@pytest.mark.parametrize(
    "statuses,expected",
    [(("PASS", "PASS"), "PASS"), (("PASS", "FAIL"), "FAIL"), (("PASS", "NOTEST"), "FAIL"),
     (("TIMEOUT",), "FAIL")],
)
def test_dfu_taken_pass_only_when_all_slots_pass(server, monkeypatch, statuses, expected):
    rows = tuple((i, s) for i, s in enumerate(statuses, 1))
    _dfu_decision(monkeypatch, TakeDecision("taken", "DFU:1", capacity=len(rows), results=rows,
                                            reason="reliable_complete"))
    res = run_check_cmd(server, {"station": "DFU", "device": "DFU-01"})
    assert res["status"] == "ok"
    assert res["result"] == expected


def test_dfu_inspection_exception_is_error(server, monkeypatch):
    def boom(kvm, gate):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(round_frame_consumer, "observe_latest_round_frame", boom)
    res = run_check_cmd(server, {"station": "DFU", "device": "DFU-01"})
    assert res["status"] == "error"
    assert res["error"] == "check_failed"
