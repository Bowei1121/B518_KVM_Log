"""inspect: the Monitor UI's read-only check (ticket #9).

It takes no device lock and never changes the round a LabVIEW check is waiting on.
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"


@pytest.fixture
def rig():
    kvm = LogSolutionKVM(state="standby")
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=KVMConnectionPool(client_factory=lambda host, password="": kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
    )
    srv.start()
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _open(srv):
    host, port = srv.server_address
    return socket.create_connection((host, port), timeout=15.0)


def _send(sock, obj):
    sock.sendall(dumps_request({"protocol_version": "1.0", **obj}))


def _recv(sock):
    return recv_reply(sock)


def _background(fn):
    out = {}
    th = threading.Thread(target=lambda: out.update(resp=fn()), daemon=True)
    th.start()
    return th, out


def test_inspect_reports_preparing_and_manual_stop_complete(rig):
    srv, kvm = rig
    kvm.show("preparing")
    with _open(srv) as ui:
        _send(ui, {"req_id": "prep", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0})
        preparing = _recv(ui)
    kvm.show("manual_stop_complete", ["STOPPED", "PASS", "FAIL", "NOTEST"])
    with _open(srv) as ui:
        _send(ui, {"req_id": "stop", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0})
        stopped = _recv(ui)

    assert preparing["status"] == "ok"
    assert preparing["data"]["kind"] == "waiting"
    assert preparing["data"]["state"] == "preparing"
    assert stopped["data"]["kind"] == "waiting"
    assert stopped["data"]["state"] == "manual_stop_complete"
    assert stopped["data"]["results"] == []


def test_inspect_is_not_blocked_by_a_labview_lock(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    labview = _open(srv)
    _send(labview, {"req_id": "lv", "device": DEVICE, "cmd": "check_finish", "timeout_sec": 10})
    time.sleep(0.3)
    assert srv.lock_manager.is_locked(DEVICE)

    with _open(srv) as ui:
        _send(ui, {"req_id": "ui", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0})
        resp = _recv(ui)

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["data"]["kind"] == "waiting"
    labview.close()


def test_inspect_never_takes_the_round_from_labview(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    labview = _open(srv)
    _send(labview, {"req_id": "lv", "device": DEVICE, "cmd": "check_finish", "timeout_sec": 15})
    time.sleep(0.3)

    time.sleep(0.3)
    kvm.show("complete", ["PASS", "FAIL", "PASS", "PASS"])
    with _open(srv) as ui:
        _send(ui, {"req_id": "ui-once", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0})
        ui_resp = _recv(ui)
    lv_resp = _recv(labview)
    labview.close()

    assert ui_resp["data"]["kind"] != "taken"
    assert ui_resp["data"]["active"] == []
    assert ui_resp["data"]["results"] == []
    assert lv_resp["status"] == "ok"
    assert lv_resp["data"]["kind"] == "taken"
    assert [row["status"] for row in lv_resp["data"]["results"]] == ["PASS", "FAIL", "PASS", "PASS"]


def test_inspect_after_labview_took_the_round_still_reports_the_screen_not_already_taken(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    labview = _open(srv)
    _send(labview, {"req_id": "lv", "device": DEVICE, "cmd": "check_finish", "timeout_sec": 15})
    time.sleep(0.3)
    kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
    assert _recv(labview)["data"]["kind"] == "taken"
    labview.close()

    with _open(srv) as ui:
        _send(ui, {"req_id": "ui-after", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0})
        resp = _recv(ui)

    assert resp["data"]["kind"] != "already_taken"
    assert srv.lock_manager.is_locked(DEVICE) is False


def test_inspect_validates_like_check(rig):
    srv, kvm = rig
    with _open(srv) as sock:
        _send(sock, {"req_id": "bad", "device": DEVICE, "cmd": "inspect"})
        resp = _recv(sock)
    assert resp["error"] == "invalid_argument"


def test_inspect_rejects_raw_kvm_ip(rig):
    srv, kvm = rig
    with _open(srv) as sock:
        _send(sock, {"req_id": "raw", "device": DEVICE, "cmd": "inspect", "timeout_sec": 0, "kvm_ip": "1.2.3.4"})
        resp = _recv(sock)
    assert resp["error"] == "forbidden_field"


def _inspect_once(srv, timeout_sec):
    with _open(srv) as ui:
        _send(ui, {"req_id": "ui-lp", "device": DEVICE, "cmd": "inspect", "timeout_sec": timeout_sec})
        return _recv(ui)
