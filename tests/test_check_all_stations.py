"""check_finish / inspect read the Log Solution band the same way on every station.

FCT and BT devices go through the same round gate as DFU: Monitoring before Complete,
two fresh equal complete frames, capacity equal to the device channels, already_taken
repeats the delivered results, Review pauses and a frozen picture is frame_frozen.
inspect reports the screen and never takes. Each test drives a real Core socket
against a synthetic Log Solution screen.
"""

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

STATIONS = ["FCT-01", "BT-01"]
ROUTES = {
    "FCT-07": {"station": "FCT", "kvm_ip": "192.168.211.7", "channels": 6},
    "FCT-12": {"station": "FCT", "kvm_ip": "192.168.211.12", "channels": 12},
    "BT-20": {"station": "BT", "kvm_ip": "192.168.212.20", "channels": 20},
    "BT-11": {"station": "BT", "kvm_ip": "192.168.212.11", "channels": 1},
}


@pytest.fixture
def rig():
    kvm = LogSolutionKVM(state="standby")
    srv = TcpJsonServer(
        device_registry=make_registry(ROUTES),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=0.3),
    )
    srv.start()
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _open(srv):
    host, port = srv.server_address
    return socket.create_connection((host, port), timeout=10.0)


def _recv(sock):
    return recv_reply(sock)


def _call(srv, device, cmd="check_finish", timeout_sec=0, req_id="r"):
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": cmd,
               "timeout_sec": timeout_sec}
    with _open(srv) as sock:
        sock.sendall(dumps_request(payload))
        return _recv(sock)


def _take(srv, kvm, device, statuses):
    """A started round, then a complete band read twice: the second read is the taken result."""
    arm_round(srv, kvm, device, ["WAITING"] * len(statuses))
    assert _call(srv, device)["data"]["kind"] == "waiting"
    kvm.show("complete", statuses)
    first = _call(srv, device)
    assert first["data"]["kind"] == "waiting"
    assert first["data"]["reason"] == "confirming_complete_frame"
    return _call(srv, device)


@pytest.mark.parametrize("device", STATIONS)
def test_two_equal_complete_frames_after_monitoring_are_taken_per_slot(rig, device):
    srv, kvm = rig

    resp = _take(srv, kvm, device, ["PASS", "FAIL", "NOTEST", "TIMEOUT"])

    assert resp["status"] == "ok"
    assert resp["data"] == {
        "kind": "taken", "state": "complete", "reason": "reliable_complete", "capacity": 4,
        "active": [],
        "results": [{"slot": 1, "status": "PASS"}, {"slot": 2, "status": "FAIL"},
                    {"slot": 3, "status": "NOTEST"}, {"slot": 4, "status": "TIMEOUT"}],
    }


@pytest.mark.parametrize("device", STATIONS)
def test_complete_without_monitoring_first_is_never_taken(rig, device):
    srv, kvm = rig
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])

    reads = [_call(srv, device) for _ in range(3)]

    for resp in reads:
        assert resp["status"] == "error"
        assert resp["error"] == "monitor_not_ready"
        assert resp["data"]["results"] == []
        assert resp["data"]["kind"] != "taken"


@pytest.mark.parametrize("device", STATIONS)
def test_after_taken_the_same_round_is_already_taken(rig, device):
    srv, kvm = rig
    assert _take(srv, kvm, device, ["PASS"] * 4)["data"]["kind"] == "taken"

    resp = _call(srv, device, timeout_sec=5)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "already_taken"
    assert resp["data"]["active"] == []
    assert resp["data"]["results"] == [{"slot": i, "status": "PASS"} for i in range(1, 5)]


@pytest.mark.parametrize("device", STATIONS)
def test_review_pauses_a_long_poll_at_once(rig, device):
    srv, kvm = rig
    kvm.show("monitoring")
    _call(srv, device)
    kvm.show("review")

    resp = _call(srv, device, timeout_sec=30)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "paused"
    assert resp["data"]["state"] == "review"
    assert resp["data"]["reason"] == "confirmation_required"
    assert resp["elapsed_sec"] < 1.0


@pytest.mark.parametrize("device", STATIONS)
def test_band_with_other_slot_count_than_device_channels_is_capacity_mismatch(rig, device):
    srv, kvm = rig

    resp = _take(srv, kvm, device, ["PASS", "FAIL"])  # two slots on a four-channel device

    assert resp["status"] == "error"
    assert resp["error"] == "capacity_mismatch"
    assert resp["data"]["kind"] != "taken"
    assert resp["data"]["results"] == []


@pytest.mark.parametrize("device", STATIONS)
def test_frozen_picture_is_frame_frozen(rig, device):
    srv, kvm = rig
    kvm.show("monitoring")
    kvm.freeze()

    resp = _call(srv, device, timeout_sec=5)

    assert resp["status"] == "error"
    assert resp["error"] == "frame_frozen"
    assert resp["data"]["results"] == []


@pytest.mark.parametrize("device", STATIONS)
def test_screen_without_log_solution_is_unknown_never_a_result(rig, device):
    srv, kvm = rig
    kvm.blank()

    resp = _call(srv, device)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "unknown"
    assert resp["data"]["capacity"] == 0
    assert resp["data"]["results"] == []


@pytest.mark.parametrize("device,channels", [("BT-11", 1), ("FCT-07", 6), ("FCT-12", 12), ("BT-20", 20)])
def test_one_to_twenty_channels_in_one_or_two_rows_are_taken(rig, device, channels):
    srv, kvm = rig
    statuses = [("PASS", "FAIL", "NOTEST", "TIMEOUT", "STOPPED")[i % 5] for i in range(channels)]

    resp = _take(srv, kvm, device, statuses)

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "taken"
    assert resp["data"]["capacity"] == channels
    assert resp["data"]["results"] == [{"slot": i, "status": s} for i, s in enumerate(statuses, 1)]


def test_second_row_holds_channels_eleven_to_n(rig):
    srv, kvm = rig
    statuses = ["PASS"] * 10 + ["FAIL", "NOTEST"] + ["PASS"] * 8  # row 2 starts FAIL, NOTEST

    resp = _take(srv, kvm, "BT-20", statuses)

    results = resp["data"]["results"]
    assert results[10] == {"slot": 11, "status": "FAIL"}
    assert results[11] == {"slot": 12, "status": "NOTEST"}
    assert results[19] == {"slot": 20, "status": "PASS"}


def _status_busy(srv, device):
    with _open(srv) as sock:
        sock.sendall(dumps_request({"protocol_version": "1.0", "req_id": "s", "device": "", "cmd": "status"}))
        devices = _recv(sock)["data"]["devices"]
    return next(d["busy"] for d in devices if d["device"] == device)


@pytest.mark.parametrize("device", STATIONS)
def test_inspect_reads_the_band_without_consuming_the_round(rig, device):
    srv, kvm = rig
    arm_round(srv, kvm, device)
    kvm.show("complete", ["PASS", "FAIL", "PASS", "NOTEST"])

    seen = _call(srv, device, cmd="inspect", timeout_sec=5)
    again = _call(srv, device, cmd="inspect", timeout_sec=5)
    _call(srv, device)
    labview = _call(srv, device)

    expected = [{"slot": 1, "status": "PASS"}, {"slot": 2, "status": "FAIL"},
                {"slot": 3, "status": "PASS"}, {"slot": 4, "status": "NOTEST"}]
    assert seen["status"] == "ok"
    assert seen["data"]["kind"] == "waiting"
    assert seen["data"]["state"] == "complete"
    assert seen["data"]["active"] == []
    assert seen["data"]["results"] == []
    assert again["data"]["kind"] == "waiting"
    assert again["data"]["state"] == "complete"
    assert again["data"]["results"] == []
    assert again["data"]["active"] == []
    assert again["data"]["kind"] != "started"
    assert labview["data"]["kind"] == "taken"
    assert labview["data"]["results"] == expected


@pytest.mark.parametrize("device", STATIONS)
def test_inspect_answers_while_a_check_holds_the_device(rig, device):
    srv, kvm = rig
    kvm.show("monitoring")
    labview = _open(srv)
    labview.sendall(dumps_request({"protocol_version": "1.0", "req_id": "lv", "device": device, "cmd": "check_finish",
                                   "timeout_sec": 30}))
    for _ in range(200):
        if _status_busy(srv, device):
            break
    assert _status_busy(srv, device) is True

    resp = _call(srv, device, cmd="inspect")

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "waiting"
    assert resp["data"]["state"] == "monitoring"
    assert resp["data"]["capacity"] == 4
    labview.close()


@pytest.mark.parametrize("device", STATIONS)
def test_inspect_without_log_solution_on_screen_is_unknown(rig, device):
    srv, kvm = rig
    kvm.blank()

    resp = _call(srv, device, cmd="inspect")

    assert resp["status"] == "ok"
    assert resp["data"]["kind"] == "unknown"
    assert resp["data"]["results"] == []
