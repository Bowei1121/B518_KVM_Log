"""start_test 1.0 on BT (Start All) over the socket and in the flow, on a synthetic BT screen (ticket #24).

The BT screen is ``frame``; the Log Solution window is read from ``latest_frame()``, the same split
as the DFU tests. Clicks are recorded in ``clicks``.
"""

import asyncio
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

from bt_flow import BtStartError, run_start_all  # noqa: E402
from fake_kvm import FakeKVM, beside, make_snapshot  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry, render_log_solution  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

START_ALL_BOX = (50, 75, 90, 110)  # x, y, w, h on the BT screen
WINDOW_BOX = (15, 20, 125, 140)
DOCK_BOX = (185, 15, 225, 55)


def _bt_screen(seed=12):
    return np.random.RandomState(seed).randint(0, 255, (180, 260, 3), dtype=np.uint8)


def _save_templates(root, frame):
    catalog = TemplateCatalog(root)
    catalog.save_crop(frame, WINDOW_BOX, "BT", "window")
    catalog.save_crop(frame, DOCK_BOX, "BT", "dock_icon")
    catalog.save_crop(frame, START_ALL_BOX, "BT", "start_all")
    return catalog


class FakeKvm(FakeKVM):
    def __init__(self, frame):
        super().__init__(frame)
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class BtKvm(FakeKvm):
    def __init__(self, frame, log_state="monitoring"):
        super().__init__(frame)
        self.log_state = log_state

    def latest_frame(self):
        now = time.monotonic()
        app = render_log_solution(self.log_state, ["WAITING"] * 4)
        return make_snapshot(beside(self.frame, app), sequence=1, received_monotonic=now,
                             stream_id="synthetic", pts=now)


class FailingKvm(BtKvm):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


@pytest.fixture
def rig(tmp_path):
    frame = _bt_screen()
    root = tmp_path / "template"
    _save_templates(root, frame)
    kvm = BtKvm(frame)
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
        yield srv, kvm, frame
    finally:
        srv.stop()
        srv.wait(timeout=2.0)


def _start_test(srv, device, req_id="st-bt"):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "start_test"}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


def test_start_all_clicks_dock_then_start_all_and_reports_action(rig):
    srv, kvm, _ = rig

    resp = _start_test(srv, "BT-01")

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["data"] == {"action": "start_all"}
    assert len(kvm.clicks) == 2
    assert kvm.clicks[0][0] > 180  # dock on the right of the screen
    assert kvm.clicks[1][0] < 140  # Start All inside the window


@pytest.mark.parametrize("state", ["standby", "complete", "review"])
def test_start_test_is_monitor_not_ready_and_clicks_nothing(rig, state):
    srv, kvm, _ = rig
    kvm.log_state = state

    resp = _start_test(srv, "BT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "monitor_not_ready"
    assert resp["data"]["action"] == ""
    assert kvm.clicks == []


def test_hid_failure_is_hid_failed(rig):
    srv, kvm, frame = rig
    failing = FailingKvm(frame)
    srv.kvm_pool = KVMConnectionPool(client_factory=lambda host, password="": failing)

    resp = _start_test(srv, "BT-01")

    assert resp["status"] == "error"
    assert resp["error"] == "hid_failed"
    assert resp["data"]["action"] == ""


def test_missing_start_all_is_hid_failed_and_never_clicks_it(rig):
    srv, kvm, frame = rig
    changed = frame.copy()
    x, y, w, h = START_ALL_BOX
    changed[y:y + h, x:x + w] = _bt_screen(seed=99)[y:y + h, x:x + w]
    kvm.frame = changed

    resp = _start_test(srv, "BT-01")

    assert resp["error"] == "hid_failed"
    assert all(not (x <= cx < x + w and y <= cy < y + h) for cx, cy in kvm.clicks)


def test_flow_refuses_without_templates_before_any_click(tmp_path):
    kvm = FakeKvm(_bt_screen())
    with pytest.raises(BtStartError) as excinfo:
        asyncio.run(run_start_all(kvm, template_root=tmp_path / "empty", log=lambda *_: None))
    assert excinfo.value.code == "hid_failed"
    assert kvm.clicks == []
