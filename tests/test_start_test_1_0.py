"""start_test 1.0 over the socket on synthetic DFU and FCT screens, plus the removed button (ticket #24).

The fakes record every click and key, so the tests assert HID actions as well as the JSON reply.
The Log Solution window is read from latest_frame(); the station screen is read from frame,
the same split as the DFU input tests. BT Start All lives in test_bt_start_test_1_0.py.
"""

import json
import socket
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dfu_helpers import BUTTON, SyntheticDfuKVM, save_templates  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]


class FailingDfuKVM(SyntheticDfuKVM):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


def _dfu(state="monitoring"):
    kvm = SyntheticDfuKVM(FOUR)
    kvm.log_state = state
    return kvm


def _clicks(kvm):
    return [(event[1], event[2]) for event in kvm.events if event[0] == "click"]


def _clicks_on_ok(kvm):
    bx, by, bw, bh = BUTTON
    return [(x, y) for x, y in _clicks(kvm) if bx <= x < bx + bw and by <= y < by + bh]


@pytest.fixture
def serve(tmp_path):
    servers = []

    def _make(kvm):
        root = tmp_path / "template"
        save_templates(root, FOUR)
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
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.stop()
        srv.wait(timeout=2.0)


def _send(srv, payload):
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def _start_test(srv, device, **extra):
    payload = {"protocol_version": "1.0", "req_id": "st-1", "device": device, "cmd": "start_test"}
    payload.update(extra)
    return _send(srv, payload)


def test_dfu_start_test_clicks_ok_once_and_types_nothing(serve):
    kvm = _dfu()
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01")

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["data"] == {"action": "ok"}
    assert len(_clicks_on_ok(kvm)) == 1
    assert not any(event[0] in ("type", "enter", "log_shortcut") for event in kvm.events)


def test_preparing_start_test_is_monitor_not_ready_and_sends_no_hid(serve):
    kvm = _dfu("preparing")
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert "preparing" in resp["message"]
    assert resp["data"]["action"] == ""
    assert kvm.events == []


@pytest.mark.parametrize("state", ["standby", "complete", "review"])
def test_start_test_is_monitor_not_ready_unless_log_solution_is_monitoring(serve, state):
    kvm = _dfu(state)
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["action"] == ""
    assert kvm.events == []


class NoFrameKVM(SyntheticDfuKVM):
    """JetKVM that has not delivered a frame yet: the Log Solution marker cannot be read."""

    def latest_frame(self):
        return None


@pytest.mark.parametrize("device", ["DFU-01", "BT-01"])
def test_start_test_without_a_frame_is_monitor_not_ready_and_sends_no_hid(serve, device):
    kvm = NoFrameKVM(FOUR)
    srv = serve(kvm)

    resp = _start_test(srv, device)

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["action"] == ""
    assert kvm.events == []


class FirstClickHid(SyntheticDfuKVM):
    """The first click reports a HID error. Later clicks do not."""

    def __init__(self, positions):
        super().__init__(positions)
        self._hid_sent = False

    async def click(self, x, y):
        await super().click(x, y)
        if not self._hid_sent:
            self._hid_sent = True
            self.rpc_errors.append("hidg endpoint disconnected")


def test_hid_failure_does_not_fail_the_next_start_test_on_the_same_kvm(serve):
    kvm = FirstClickHid(FOUR)
    kvm.log_state = "monitoring"
    srv = serve(kvm)

    first = _start_test(srv, "DFU-01", req_id="st-1")
    second = _start_test(srv, "DFU-01", req_id="st-2")

    assert first["status"] == "error"
    assert first["error"] == "hid_failed"
    assert first["message"] == "Dock HID 錯誤: hidg endpoint disconnected"
    assert second["status"] == "ok"
    assert second["error"] == ""
    assert second["data"] == {"action": "ok"}
    assert len(_clicks_on_ok(kvm)) == 1


def test_dfu_hid_failure_is_hid_failed_and_no_ok_click(serve):
    kvm = FailingDfuKVM(FOUR)
    kvm.log_state = "monitoring"
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01")

    assert resp["status"] == "error"
    assert resp["error"] == "hid_failed"
    assert resp["data"]["action"] == ""
    assert _clicks_on_ok(kvm) == []


def test_missing_ok_button_is_hid_failed_and_never_clicks_it(serve):
    kvm = _dfu()
    kvm.button_patch = np.random.RandomState(99).randint(0, 255, (BUTTON[3], BUTTON[2], 3), dtype=np.uint8)
    kvm.frame = kvm._render()
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01")

    assert resp["error"] == "hid_failed"
    assert _clicks_on_ok(kvm) == []


def test_start_test_on_fct_is_unsupported_command_and_sends_nothing(serve):
    kvm = LogSolutionKVM(state="monitoring")
    srv = serve(kvm)

    resp = _start_test(srv, "FCT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "unsupported_command"
    assert kvm.events == []


@pytest.mark.parametrize("device", ["DFU-01", "BT-01", "FCT-01"])
def test_button_is_removed_and_answers_unknown_command(serve, device):
    kvm = _dfu()
    srv = serve(kvm)

    resp = _send(srv, {"protocol_version": "1.0", "req_id": "b-1", "device": device,
                       "cmd": "button", "action": "start_all"})

    assert resp["status"] == "error"
    assert resp["error"] == "unknown_command"
    assert kvm.events == []


@pytest.mark.parametrize(("extra", "error"), [
    ({"action": "ok"}, "unknown_field"),
    ({"items": []}, "unknown_field"),
    ({"kvm_ip": "192.168.200.1"}, "forbidden_field"),
])
def test_start_test_takes_no_command_fields(serve, extra, error):
    kvm = _dfu()
    srv = serve(kvm)

    resp = _start_test(srv, "DFU-01", **extra)

    assert resp["error"] == error
    assert kvm.events == []


def test_start_test_is_device_locked(serve):
    kvm = _dfu()
    srv = serve(kvm)
    assert srv.lock_manager.try_acquire("DFU-01")

    resp = _start_test(srv, "DFU-01")

    assert resp["error"] == "device_busy"
    assert kvm.events == []
