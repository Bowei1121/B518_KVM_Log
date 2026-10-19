"""Round-start request rules for check_finish (ticket #44, spec #40).

Socket tests against LogSolutionKVM. A round-start request exists only after monitor_start
sends the shortcut or sees Preparing. taken and manual_verification_required clear it.
"""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

SHORTCUT = ("key", "cmd+shift+m")
TERMINAL = ["PASS", "FAIL", "NOTEST", "TIMEOUT"]


def _save_dock_template(root):
    kvm = LogSolutionKVM()
    frame = kvm.latest_frame()[0]
    x, y, w, h = kvm.dock_icon_box()
    TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                    "log_solution_dock_icon")


@pytest.fixture
def serve(tmp_path):
    servers = []

    def _make(kvm):
        root = tmp_path / "template"
        _save_dock_template(root)
        srv = TcpJsonServer(
            device_registry=make_registry(),
            host="127.0.0.1",
            port=0,
            kvm_pool=make_log_solution_pool(kvm),
            freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
            template_root=root,
            monitor_confirm_sec=0.3,
        )
        srv.start()
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, payload):
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _monitor_start(srv, timeout_sec=1, req_id="ms"):
    return _send(srv, {"protocol_version": "1.0", "req_id": req_id, "device": "DFU-01",
                       "cmd": "monitor_start", "timeout_sec": timeout_sec})


def _finish(srv, timeout_sec=0, req_id="cf"):
    return _send(srv, {"protocol_version": "1.0", "req_id": req_id, "device": "DFU-01",
                       "cmd": "check_finish", "timeout_sec": timeout_sec})


def _take(srv, kvm, statuses):
    kvm.show("complete", statuses)
    assert _finish(srv, req_id="arm")["data"]["kind"] == "waiting"
    return _finish(srv, req_id="take")


def test_check_finish_without_a_start_request_is_monitor_not_ready(serve):
    kvm = LogSolutionKVM(state="complete", statuses=TERMINAL)
    srv = serve(kvm)

    resp = _finish(srv)

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["results"] == []
    assert resp["data"]["kind"] != "taken"
    assert kvm.events == []


def test_preparing_timeout_keeps_the_request_until_manual_verification(serve):
    """2026-10-19 supplement 1: monitor_not_started does not clear the round-start request.

    Preparing establishes the request and sends nothing. Review still pauses. The confirmed
    complete screen is manual_verification_required, and that reply clears the request.
    """
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)

    started = _monitor_start(srv, timeout_sec=1)
    assert started["error"] == "monitor_not_started"
    assert kvm.events == []

    kvm.show("review")
    paused = _finish(srv, req_id="pause")
    assert paused["status"] == "ok"
    assert paused["data"]["kind"] == "paused"
    assert kvm.events == []

    kvm.show("manual_stop_complete", ["STOPPED", "PASS", "FAIL", "NOTEST"])
    verdict = _finish(srv, req_id="verify")
    assert verdict["status"] == "error"
    assert verdict["error"] == "manual_verification_required"
    assert verdict["data"]["results"] == []
    assert kvm.events == []

    again = _finish(srv, req_id="cleared")
    assert again["error"] == "monitor_not_ready"
    assert again["data"]["results"] == []


def test_shortcut_then_manual_stop_without_monitoring_needs_verification(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.state_after_shortcut = "manual_stop_complete"
    srv = serve(kvm)

    started = _monitor_start(srv, timeout_sec=1)
    assert started["error"] == "monitor_not_started"
    assert SHORTCUT in kvm.events
    hid_before = len(kvm.events)

    kvm.show("manual_stop_complete", ["STOPPED", "PASS", "FAIL", "NOTEST"])
    verdict = _finish(srv)

    assert verdict["error"] == "manual_verification_required"
    assert verdict["data"]["results"] == []
    assert len(kvm.events) == hid_before


def test_delivered_signature_change_without_a_new_round_is_monitor_not_ready(serve):
    """2026-10-19 supplement 2: decision order, first match wins.

    Review and an unreadable screen win before the request check. After delivery the same
    signature is already_taken even though the request was cleared. A different signature
    with no new-round signal is monitor_not_ready, not a second taken.
    """
    kvm = LogSolutionKVM(state="review")
    srv = serve(kvm)

    paused = _finish(srv, req_id="review")
    assert paused["data"]["kind"] == "paused"

    kvm.blank()
    unknown = _finish(srv, req_id="blank")
    assert unknown["status"] == "ok"
    assert unknown["data"]["kind"] == "unknown"

    kvm.show("standby")
    assert _monitor_start(srv, req_id="start")["data"]["state"] == "monitoring"
    taken = _take(srv, kvm, TERMINAL)
    assert taken["data"]["kind"] == "taken"
    delivered = taken["data"]["results"]

    replay = _finish(srv, req_id="replay")
    assert replay["data"]["kind"] == "already_taken"
    assert replay["data"]["results"] == delivered

    kvm.show("complete", ["FAIL", "PASS", "NOTEST", "TIMEOUT"])
    changed = _finish(srv, req_id="changed")
    assert changed["status"] == "error"
    assert changed["error"] == "monitor_not_ready"
    assert changed["data"]["results"] == []
    assert changed["data"]["kind"] != "taken"
