"""monitor_start 1.0 over the socket on a synthetic ATE screen (ticket #23).

Each test drives a real Core socket against LogSolutionKVM. The fake records the Dock click and
the Cmd+Shift+M key, so the tests assert the order of HID actions as well as the JSON reply.
Confirm and start timeouts are injected short; no test waits for the production 3 s / 10 s.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

STATIONS = ["DFU-01", "FCT-01", "BT-01"]
CONFIRM_SEC = 0.3
START_TIMEOUT_SEC = 0.6
SHORTCUT = ("key", "cmd+shift+m")


def _save_dock_template(root, kvm):
    frame = kvm.latest_frame()[0]
    x, y, w, h = kvm.dock_icon_box()
    TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                    "log_solution_dock_icon")


def _dock_click(kvm):
    x, y, w, h = kvm.dock_icon_box()
    return ("click", int(x + w / 2), int(y + h / 2))


def _dock_recovery(kvm):
    """Dock click, then the pointer parked at the top edge mid-screen (no click)."""
    frame = kvm.latest_frame()[0]
    return [_dock_click(kvm), ("move", frame.shape[1] // 2, 1)]


@pytest.fixture
def serve(tmp_path):
    servers = []

    def _make(kvm, save_template=True):
        root = tmp_path / "template"
        if save_template:
            # The operator captured the icon once from a healthy ATE screen, not from the screen under test.
            _save_dock_template(root, LogSolutionKVM())
        srv = TcpJsonServer(
            device_registry=make_registry(),
            host="127.0.0.1",
            port=0,
            kvm_pool=make_log_solution_pool(kvm),
            freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
            template_root=root,
            monitor_confirm_sec=CONFIRM_SEC,
            monitor_start_timeout_sec=START_TIMEOUT_SEC,
        )
        srv.start()
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.stop()
        srv.wait(timeout=2.0)


def _open(srv):
    host, port = srv.server_address
    return socket.create_connection((host, port), timeout=10.0)


def _recv(sock):
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _send(srv, payload):
    with _open(srv) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        return _recv(sock)


def _monitor_start(srv, device, req_id="ms", timeout_sec=1):
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "monitor_start"}
    if timeout_sec is not None:
        payload["timeout_sec"] = timeout_sec
    return _send(srv, payload)


def _check(srv, device):
    return _send(srv, {"protocol_version": "1.0", "req_id": "ck", "device": device, "cmd": "check",
                       "timeout_sec": 0})


@pytest.mark.parametrize("device", STATIONS)
@pytest.mark.parametrize("start_state", ["standby", "complete"])
def test_success_clicks_dock_then_shortcut_and_reports_monitoring(serve, device, start_state):
    kvm = LogSolutionKVM(state=start_state, statuses=["PASS"] * 4 if start_state == "complete" else ())
    srv = serve(kvm)

    resp = _monitor_start(srv, device)

    assert resp["status"] == "ok"
    assert resp["data"] == {"state": "monitoring", "capacity": 4}
    assert kvm.events == [*_dock_recovery(kvm), SHORTCUT]


@pytest.mark.parametrize("device", STATIONS)
def test_monitor_start_arms_the_round_so_check_takes_the_next_complete(serve, device):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm)
    assert _monitor_start(srv, device)["status"] == "ok"

    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
    first = _check(srv, device)
    final = _check(srv, device)

    assert first["data"]["kind"] == "waiting"
    assert first["data"]["reason"] == "confirming_complete_frame"
    assert final["status"] == "ok"
    assert final["data"]["kind"] == "taken"
    assert [r["status"] for r in final["data"]["results"]] == ["PASS", "FAIL", "NOTEST", "TIMEOUT"]


@pytest.mark.parametrize("device", STATIONS)
def test_monitoring_is_busy_and_nothing_is_sent(serve, device):
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)

    resp = _monitor_start(srv, device)

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_busy"
    assert kvm.events == []


@pytest.mark.parametrize("device", STATIONS)
def test_review_before_the_shortcut_is_log_solution_review_and_nothing_is_sent(serve, device):
    kvm = LogSolutionKVM(state="review")
    srv = serve(kvm)

    resp = _monitor_start(srv, device)

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_review"
    assert kvm.events == []


def test_missing_dock_template_is_hid_failed_and_names_the_file(serve):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm, save_template=False)

    resp = _monitor_start(srv, "FCT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "hid_failed"
    assert "log_solution_dock_icon" in resp["message"]
    assert kvm.events == []


def test_no_dock_icon_on_screen_is_not_found_and_nothing_is_clicked(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.dock_visible = False
    srv = serve(kvm)

    resp = _monitor_start(srv, "BT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_not_found"
    assert kvm.events == []


def test_no_log_solution_after_the_dock_click_is_not_found_and_no_shortcut(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.close_window()
    srv = serve(kvm)

    resp = _monitor_start(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_not_found"
    assert kvm.events == _dock_recovery(kvm)


def test_monitoring_after_the_dock_click_is_busy_and_no_shortcut(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.close_window()
    kvm.restore_on_dock = True
    kvm.state_on_dock = "monitoring"
    srv = serve(kvm)

    resp = _monitor_start(srv, "FCT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_busy"
    assert kvm.events == _dock_recovery(kvm)


def test_closed_log_solution_is_reopened_by_the_dock_click_then_started(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.close_window()
    kvm.restore_on_dock = True
    srv = serve(kvm)

    resp = _monitor_start(srv, "BT-01")

    assert resp["status"] == "ok"
    assert resp["data"] == {"state": "monitoring", "capacity": 4}
    assert kvm.events == [*_dock_recovery(kvm), SHORTCUT]


def test_manual_stop_complete_uses_the_dock_flow_and_sends_the_shortcut(serve):
    kvm = LogSolutionKVM(state="manual_stop_complete", statuses=["STOPPED"] * 4)
    srv = serve(kvm)

    resp = _monitor_start(srv, "DFU-01")

    assert resp["status"] == "ok"
    assert resp["data"]["state"] == "monitoring"
    assert kvm.events == [*_dock_recovery(kvm), SHORTCUT]


def test_preparing_at_start_waits_without_a_shortcut_then_monitoring_is_seen(serve):
    """2026-10-19: a preparing screen establishes the round-start request and this call's monitoring counts."""
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)
    out = {}

    def run():
        out["resp"] = _monitor_start(srv, "DFU-01")

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.15)
    kvm.show("monitoring")
    th.join(timeout=5)

    assert out["resp"]["status"] == "ok"
    assert out["resp"]["data"]["state"] == "monitoring"
    assert SHORTCUT not in kvm.events
    assert not any(event[0] == "click" for event in kvm.events)
    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
    first = _check(srv, "DFU-01")
    second = _check(srv, "DFU-01")
    assert first["data"]["kind"] == "waiting"
    assert second["data"]["kind"] == "taken"


def test_preparing_then_review_returns_log_solution_review_without_a_shortcut(serve):
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)
    out = {}

    def run():
        out["resp"] = _monitor_start(srv, "FCT-01")

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.15)
    kvm.show("review")
    th.join(timeout=5)

    assert out["resp"]["status"] == "error"
    assert out["resp"]["error"] == "log_solution_review"
    assert SHORTCUT not in kvm.events
    assert not any(event[0] == "click" for event in kvm.events)


def test_preparing_until_timeout_is_monitor_not_started_and_sends_nothing(serve):
    kvm = LogSolutionKVM(state="preparing")
    srv = serve(kvm)

    resp = _monitor_start(srv, "BT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_started"
    assert kvm.events == []
    assert resp["elapsed_sec"] < 3.0


def test_preparing_after_the_shortcut_keeps_waiting_until_monitoring(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.state_after_shortcut = "preparing"
    srv = serve(kvm)
    out = {}

    def run():
        out["resp"] = _monitor_start(srv, "DFU-01")

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.4)
    kvm.show("monitoring")
    th.join(timeout=5)

    assert out["resp"]["status"] == "ok"
    assert SHORTCUT in kvm.events


def test_review_after_the_shortcut_is_log_solution_review_at_once(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.state_after_shortcut = "review"
    srv = serve(kvm)

    resp = _monitor_start(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_review"
    assert SHORTCUT in kvm.events
    assert resp["elapsed_sec"] < 0.8


@pytest.mark.parametrize("timeout_sec", [None, 0, 301, -1, "soon", True])
def test_monitor_start_timeout_out_of_range_is_invalid_argument(serve, timeout_sec):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm)

    resp = _monitor_start(srv, "DFU-01", timeout_sec=timeout_sec)

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert kvm.events == []


def test_shortcut_that_does_nothing_is_monitor_not_started_within_the_timeout(serve):
    kvm = LogSolutionKVM(state="standby")
    kvm.shortcut_enabled = False
    srv = serve(kvm)

    resp = _monitor_start(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_started"
    assert resp["data"]["state"] != "monitoring"
    assert kvm.events == [*_dock_recovery(kvm), SHORTCUT]
    assert resp["elapsed_sec"] < START_TIMEOUT_SEC + 2.0
