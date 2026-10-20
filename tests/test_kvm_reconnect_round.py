"""KVM reconnect inside one Core process (#56): a re-ask of the same round, seen over the socket."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from freeze_guard import FrameFreezeGuard  # noqa: E402
from protocol_helpers import LogSolutionKVM, arm_round, make_log_solution_pool, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"
PASS4 = ["PASS", "PASS", "PASS", "PASS"]


@pytest.fixture
def rig(tmp_path):
    kvm = LogSolutionKVM(state="standby")
    root = tmp_path / "template"
    root.mkdir()
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
    time.sleep(0.05)
    try:
        yield srv, kvm
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, cmd, timeout_sec=0, req_id="r", device=DEVICE):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": cmd,
               "timeout_sec": timeout_sec}
    with socket.create_connection((host, port), timeout=15.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def test_reask_of_taken_round_after_reconnect_is_already_taken_with_original_results(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("complete", PASS4)
    delivered = _send(srv, "check_finish", 2, "first")
    assert delivered["data"]["kind"] == "taken"

    kvm.stream_id = "after-reconnect"
    again = _send(srv, "check_finish", 2, "reask")

    assert again["status"] == "ok"
    assert again["data"]["kind"] == "already_taken"
    assert again["data"]["results"] == delivered["data"]["results"]


def test_inspect_seeing_new_stream_first_does_not_let_stale_complete_be_taken(rig):
    """The Monitor UI's inspect reads the reconnected stream before check does; check must still disarm (#56)."""
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("complete", PASS4)
    kvm.stream_id = "after-reconnect"
    _send(srv, "inspect", 0, "inspect-after")

    res = _send(srv, "check_finish", 2, "check-after")

    assert res["data"]["kind"] != "taken"


def test_reask_after_reconnect_with_changed_screen_is_not_already_taken(rig):
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)
    kvm.show("complete", PASS4)
    assert _send(srv, "check_finish", 2, "first")["data"]["kind"] == "taken"

    kvm.stream_id = "after-reconnect"
    kvm.show("complete", ["PASS", "FAIL", "PASS", "PASS"])
    again = _send(srv, "check_finish", 2, "reask")

    assert again.get("data", {}).get("kind") != "already_taken"
    assert again.get("error") == "monitor_not_ready"


def test_reconnect_while_armed_then_result_is_manual_verification_required(rig):
    """Case A (#56, unchanged): the stream drops before the round is taken; a result on the new stream is not taken."""
    srv, kvm = rig
    arm_round(srv, kvm, DEVICE)

    kvm.stream_id = "after-reconnect"
    kvm.show("complete", PASS4)
    reply = _send(srv, "check_finish", 2, "after")

    assert reply.get("error") == "manual_verification_required"
