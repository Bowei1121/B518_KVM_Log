"""BT button 1.0 (Start All) over the socket and in the flow, on a synthetic BT screen."""

import asyncio
import json
import socket
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from bt_flow import BtButtonError, run_start_all  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry  # noqa: E402
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


class FakeKvm:
    def __init__(self, frame):
        self.frame = frame
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FailingKvm(FakeKvm):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


@pytest.fixture
def rig(tmp_path):
    frame = _bt_screen()
    root = tmp_path / "template"
    _save_templates(root, frame)
    kvm = FakeKvm(frame)
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


def _button(srv, device, action, req_id="btn-1"):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": device, "cmd": "button"}
    if action is not None:
        payload["action"] = action
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def test_start_all_clicks_dock_then_start_all_and_reports_action(rig):
    srv, kvm, _ = rig

    resp = _button(srv, "BT-01", "start_all")

    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["data"] == {"action": "start_all"}
    assert len(kvm.clicks) == 2
    assert kvm.clicks[0][0] > 180  # dock on the right of the screen
    assert kvm.clicks[1][0] < 140  # Start All inside the window


@pytest.mark.parametrize("action", ["stop_all", "START_ALL", "", None])
def test_actions_other_than_start_all_are_invalid_argument(rig, action):
    srv, kvm, _ = rig

    resp = _button(srv, "BT-01", action)

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert kvm.clicks == []


def test_button_on_a_dfu_device_is_unsupported_command(rig):
    srv, kvm, _ = rig

    resp = _button(srv, "DFU-01", "start_all")

    assert resp["error"] == "unsupported_command"
    assert kvm.clicks == []


def test_hid_failure_is_hid_failed(rig):
    srv, kvm, frame = rig
    failing = FailingKvm(frame)
    srv.kvm_pool = KVMConnectionPool(client_factory=lambda host, password="": failing)

    resp = _button(srv, "BT-01", "start_all")

    assert resp["status"] == "error"
    assert resp["error"] == "hid_failed"
    assert resp["data"]["action"] == ""


def test_missing_start_all_is_hid_failed_and_never_clicks_it(rig, tmp_path):
    srv, kvm, frame = rig
    changed = frame.copy()
    x, y, w, h = START_ALL_BOX
    changed[y:y + h, x:x + w] = _bt_screen(seed=99)[y:y + h, x:x + w]
    kvm.frame = changed

    resp = _button(srv, "BT-01", "start_all")

    assert resp["error"] == "hid_failed"
    assert all(not (x <= cx < x + w and y <= cy < y + h) for cx, cy in kvm.clicks)


def test_flow_refuses_without_templates_before_any_click(tmp_path):
    kvm = FakeKvm(_bt_screen())
    with pytest.raises(BtButtonError) as excinfo:
        asyncio.run(run_start_all(kvm, template_root=tmp_path / "empty", log=lambda *_: None))
    assert excinfo.value.code == "hid_failed"
    assert kvm.clicks == []
