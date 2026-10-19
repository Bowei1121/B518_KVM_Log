"""DFU input 1.0 over the socket: refusals, validation and successful entry (ticket #7)."""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dfu_helpers import BUTTON, SyntheticDfuKVM, save_templates  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
DFU_IP = "192.168.200.1"


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "template"
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        template_root=root,
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _input(srv, device, items, req_id="in-1"):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "input"}
    if items is not None:
        payload["items"] = items
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def _hid_events(kvm):
    return [event for event in kvm.events if event[0] in ("click", "type", "enter", "log_shortcut")]


def test_standby_input_enters_requested_slots(rig):
    srv, kvm = rig
    kvm.log_state = "standby"

    resp = _input(srv, "DFU-01", [{"slot": 3, "sn": "F5K2:003,B"}, {"slot": 1, "sn": "SN1"}])

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["data"] == {"entered": [1, 3]}
    assert kvm.checked_channels() == [1, 3]
    assert ("type", "F5K2:003,B") in kvm.events


def test_input_never_clicks_ok_or_sends_the_shortcut(rig):
    srv, kvm = rig
    kvm.log_state = "standby"

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}, {"slot": 3, "sn": "SN3"}])

    assert resp["status"] == "ok"
    bx, by, bw, bh = BUTTON
    ok_clicks = [e for e in kvm.events if e[0] == "click" and bx <= e[1] < bx + bw and by <= e[2] < by + bh]
    assert ok_clicks == []
    assert not any(event[0] == "log_shortcut" for event in kvm.events)
    assert kvm.events[-1] == ("enter",)


def test_preparing_refuses_input_without_any_hid_action(rig):
    srv, kvm = rig
    kvm.log_state = "preparing"

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_busy"
    assert resp["data"]["entered"] == []
    assert _hid_events(kvm) == []


def test_monitoring_refuses_input_without_any_hid_action(rig):
    srv, kvm = rig
    kvm.log_state = "monitoring"

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["status"] == "error"
    assert resp["error"] == "log_solution_busy"
    assert resp["data"]["entered"] == []
    assert _hid_events(kvm) == []


def test_review_refuses_input_without_any_hid_action(rig):
    srv, kvm = rig
    kvm.log_state = "review"

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["error"] == "log_solution_busy"
    assert _hid_events(kvm) == []


def test_manual_stop_complete_accepts_input_only_after_the_round_is_settled(rig):
    """2026-10-19: manual_stop_complete is READY, same as complete.

    Input is refused until this round was delivered or manual_verification_required was returned.
    """
    srv, kvm = rig
    terminal = ["STOPPED", "PASS", "FAIL", "NOTEST"]
    kvm.log_state = "manual_stop_complete"
    kvm.log_statuses = terminal

    busy = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}], req_id="busy")

    assert busy["error"] == "log_solution_busy"
    assert _hid_events(kvm) == []

    kvm.log_state = "preparing"
    started = _cmd(srv, "DFU-01", "monitor_start", {"timeout_sec": 1}, req_id="ms")
    assert started["error"] == "monitor_not_started"
    assert _hid_events(kvm) == []

    kvm.log_state = "manual_stop_complete"
    kvm.log_statuses = terminal
    verdict = _cmd(srv, "DFU-01", "check_finish", {"timeout_sec": 0}, req_id="cf")
    assert verdict["error"] == "manual_verification_required"
    assert verdict["data"]["results"] == []
    assert _hid_events(kvm) == []

    allowed = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN-NEXT"}], req_id="next")
    assert allowed["status"] == "ok"
    assert allowed["data"]["entered"] == [1]
    assert ("type", "SN-NEXT") in kvm.events


def _cmd(srv, device, cmd, extra, req_id):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": cmd, **extra}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def test_complete_round_not_yet_taken_refuses_input(rig):
    srv, kvm = rig
    kvm.log_state = "complete"

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["error"] == "log_solution_busy"
    assert _hid_events(kvm) == []


def test_unreadable_log_solution_state_is_refused(rig):
    srv, kvm = rig
    kvm.log_state = None

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["error"] == "hid_failed"
    assert _hid_events(kvm) == []


@pytest.mark.parametrize("items", [
    [{"slot": 5, "sn": "SN5"}],
    [{"slot": 1, "sn": ""}],
    [{"slot": 1, "sn": "A"}, {"slot": 1, "sn": "B"}],
    [],
    None,
])
def test_invalid_items_are_invalid_argument_and_do_no_hid(rig, items):
    srv, kvm = rig
    kvm.log_state = "standby"

    resp = _input(srv, "DFU-01", items)

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert _hid_events(kvm) == []


def test_input_on_a_non_dfu_device_is_unsupported_command(rig):
    srv, kvm = rig

    resp = _input(srv, "FCT-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["error"] == "unsupported_command"
    assert _hid_events(kvm) == []


def test_checkbox_count_mismatch_is_hid_failed_and_types_nothing(rig, tmp_path):
    srv, kvm = rig
    kvm.log_state = "standby"
    kvm.positions = FOUR[:3]
    kvm.state = kvm.state[:3]
    kvm.frame = kvm._render()

    resp = _input(srv, "DFU-01", [{"slot": 1, "sn": "SN1"}])

    assert resp["status"] == "error"
    assert resp["error"] == "hid_failed"
    assert resp["data"]["entered"] == []
    assert not any(event[0] == "type" for event in kvm.events)
