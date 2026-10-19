"""check / inspect visibility recovery and round_lost over the socket (ticket #25).

Each test drives a real Core socket against LogSolutionKVM. The fake records the Dock click and
the Cmd+Shift+M key, so the tests assert the HID actions as well as the JSON reply.
The 30 s / 5 s / 3 attempt constants are injected short; no test waits for the production values.
Ticket #31: any unknown reading that is not a picture/stream problem counts as "Log Solution not
visible" (including a partly covered window); only a reliable reading resets the timer.
"""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

VISIBILITY_LOST_SEC = 0.3
DOCK_RECHECK_SEC = 0.3
MAX_DOCK_RECOVERIES = 3


def _save_dock_template(root, kvm):
    frame = kvm.latest_frame()[0]
    x, y, w, h = kvm.dock_icon_box()
    TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                    "log_solution_dock_icon")


def _dock_click(kvm):
    x, y, w, h = kvm.dock_icon_box()
    return ("click", int(x + w / 2), int(y + h / 2))


def _neutral_move(kvm):
    """After a Dock click the pointer is parked at the top edge, mid-screen (no click, no hot corner)."""
    frame = kvm.latest_frame()[0]
    return ("move", frame.shape[1] // 2, 1)


def _dock_recovery(kvm):
    return [_dock_click(kvm), _neutral_move(kvm)]


@pytest.fixture
def serve(tmp_path):
    servers = []

    def _make(kvm):
        root = tmp_path / "template"
        # The operator captured the icon once from a healthy ATE screen, not from the screen under test.
        _save_dock_template(root, LogSolutionKVM())
        srv = TcpJsonServer(
            device_registry=make_registry(),
            host="127.0.0.1",
            port=0,
            kvm_pool=make_log_solution_pool(kvm),
            freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
            template_root=root,
            visibility_lost_sec=VISIBILITY_LOST_SEC,
            dock_recheck_sec=DOCK_RECHECK_SEC,
            max_dock_recoveries=MAX_DOCK_RECOVERIES,
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


def _check(srv, device, timeout_sec=0):
    return _send(srv, {"protocol_version": "1.0", "req_id": "ck", "device": device, "cmd": "check_finish",
                       "timeout_sec": timeout_sec})


def _inspect(srv, device, timeout_sec=0):
    return _send(srv, {"protocol_version": "1.0", "req_id": "ins", "device": device, "cmd": "inspect",
                       "timeout_sec": timeout_sec})


def test_monitoring_then_standby_returns_round_lost_at_once(serve):
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)
    arm_round(srv, kvm, "DFU-01")
    armed = _check(srv, "DFU-01")
    assert armed["data"]["reason"] == "monitoring"

    kvm.show("standby")
    started = time.monotonic()
    resp = _check(srv, "DFU-01", timeout_sec=30)

    assert time.monotonic() - started < 5
    assert resp["status"] == "error"
    assert resp["error"] == "round_lost"
    assert resp["data"]["kind"] == "unknown"


def test_standby_without_monitoring_this_round_is_just_waiting(serve):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm)

    resp = _check(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["results"] == []


def test_preparing_resets_the_visibility_timer_and_does_not_click_the_dock(tmp_path):
    # Lost budget is longer than one 0.5 s poll, so the preparing frame can be seen and reset the timer.
    kvm = LogSolutionKVM(state="standby")
    root = tmp_path / "template"
    _save_dock_template(root, LogSolutionKVM())
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        template_root=root,
        visibility_lost_sec=1.0,
        dock_recheck_sec=0.2,
        max_dock_recoveries=MAX_DOCK_RECOVERIES,
    )
    srv.start()
    try:
        arm_round(srv, kvm, "DFU-01")
        kvm.blank()
        import threading
        out = {}

        def run():
            out["resp"] = _check(srv, "DFU-01", timeout_sec=1.6)

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.15)
        kvm.show("preparing")
        th.join(timeout=5)
        resp = out["resp"]
    finally:
        srv.stop()
        srv.wait(timeout=2.0)

    assert resp["status"] == "timeout"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "preparing"
    assert kvm.events == []


def test_covered_window_is_recovered_by_one_dock_click_and_check_takes_the_round(serve):
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)
    arm_round(srv, kvm, "DFU-01")
    assert _check(srv, "DFU-01")["data"]["reason"] == "monitoring"  # the round is armed before the cover

    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
    kvm.cover()
    kvm.restore_on_dock = True
    resp = _check(srv, "DFU-01", timeout_sec=10)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "taken"
    assert [slot["status"] for slot in resp["data"]["results"]] == ["PASS", "FAIL", "NOTEST", "TIMEOUT"]
    assert kvm.events == _dock_recovery(kvm)


def test_covered_window_after_max_dock_clicks_returns_log_solution_not_visible(serve):
    kvm = LogSolutionKVM(state="complete", statuses=["PASS"] * 4)
    kvm.cover()
    srv = serve(kvm)

    resp = _check(srv, "DFU-01", timeout_sec=30)

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_not_visible"
    assert resp["data"]["kind"] == "unknown"
    assert kvm.events == _dock_recovery(kvm) * MAX_DOCK_RECOVERIES


def test_inspect_on_covered_window_reports_only_and_never_clicks(serve):
    kvm = LogSolutionKVM(state="complete", statuses=["PASS"] * 4)
    kvm.cover()
    kvm.restore_on_dock = True
    srv = serve(kvm)

    resp = _inspect(srv, "DFU-01", timeout_sec=1.5)

    assert resp["data"]["kind"] == "unknown"
    assert kvm.events == []


def test_single_check_read_on_covered_window_never_clicks(serve):
    kvm = LogSolutionKVM(state="complete", statuses=["PASS"] * 4)
    kvm.cover()
    srv = serve(kvm)

    resp = _check(srv, "DFU-01")

    assert resp["data"]["kind"] == "unknown"
    assert kvm.events == []


def test_monitor_start_armed_round_returns_round_lost_when_standby_appears(serve):
    kvm = LogSolutionKVM(state="standby")
    srv = serve(kvm)
    started = _send(srv, {"protocol_version": "1.0", "req_id": "ms", "device": "BT-01",
                          "cmd": "monitor_start", "timeout_sec": 1})
    assert started["data"]["state"] == "monitoring"

    kvm.show("standby")
    resp = _check(srv, "BT-01", timeout_sec=30)

    assert resp["error"] == "round_lost"
    assert resp["data"]["kind"] == "unknown"


# --- Ticket #31: partly covered Log Solution -------------------------------------------------------

def _armed_complete_behind_cover(serve, cover):
    """Arm a round, then show its Complete screen behind an ATE window that covers the left side."""
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)
    arm_round(srv, kvm, "DFU-01")
    assert _check(srv, "DFU-01")["data"]["reason"] == "monitoring"
    kvm.show("complete", ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
    cover(kvm)
    return kvm, srv


@pytest.mark.parametrize("colour", ["grey", "black", "noise"])
def test_left_side_covering_only_result_cells_is_recovered_by_a_dock_click(serve, colour):
    # Cut at 40 units: cells 1-2 hidden, the left locator (x=82) still on screen.
    kvm, srv = _armed_complete_behind_cover(serve, lambda k: k.cover_left(40, colour))
    kvm.restore_on_dock = True

    resp = _check(srv, "DFU-01", timeout_sec=10)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "taken"
    assert [slot["status"] for slot in resp["data"]["results"]] == ["PASS", "FAIL", "NOTEST", "TIMEOUT"]
    assert kvm.events == _dock_recovery(kvm)


def test_covering_only_the_result_band_is_recovered_by_a_dock_click(serve):
    # The locators and marker row stay visible; only the band (y >= 32 units) is covered.
    kvm, srv = _armed_complete_behind_cover(serve, lambda k: k.cover_left(376, "grey", y_range=(32, 110)))
    kvm.restore_on_dock = True

    resp = _check(srv, "DFU-01", timeout_sec=10)

    assert resp["data"]["kind"] == "taken"
    assert kvm.events == _dock_recovery(kvm)


def test_partly_covered_window_that_never_comes_back_returns_not_visible_with_last_reason(serve):
    kvm, srv = _armed_complete_behind_cover(serve, lambda k: k.cover_left(40, "grey"))

    resp = _check(srv, "DFU-01", timeout_sec=30)

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_not_visible"
    assert resp["data"]["kind"] == "unknown"
    assert resp["data"]["reason"].startswith("result_cell_unknown_")
    assert kvm.events == _dock_recovery(kvm) * MAX_DOCK_RECOVERIES


def test_inspect_on_partly_covered_window_never_clicks(serve):
    kvm = LogSolutionKVM(state="complete", statuses=["PASS"] * 4)
    kvm.cover_left(40, "grey")
    kvm.restore_on_dock = True
    srv = serve(kvm)

    resp = _inspect(srv, "DFU-01", timeout_sec=1.5)

    assert resp["data"]["kind"] == "unknown"
    assert kvm.events == []


def test_duplicate_frames_neither_count_as_unseen_nor_reset_the_timer(serve):
    # A stream that keeps repeating its frame number: no reliable reading, but not "not visible" either.
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)
    arm_round(srv, kvm, "DFU-01")
    assert _check(srv, "DFU-01")["data"]["reason"] == "monitoring"
    kvm.repeat_sequence = True

    resp = _check(srv, "DFU-01", timeout_sec=1.5)

    assert resp["status"] == "timeout"
    assert kvm.events == []


def test_dock_icon_missing_during_check_recovery_is_log_solution_not_found_at_once(serve):
    kvm, srv = _armed_complete_behind_cover(serve, lambda k: k.cover())
    kvm.dock_visible = False

    started = time.monotonic()
    resp = _check(srv, "DFU-01", timeout_sec=30)

    assert time.monotonic() - started < 5
    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_not_found"
    assert kvm.events == []
