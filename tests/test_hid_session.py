"""HidSession: find a template, refuse a miss, and refuse a HID error (ticket #72)."""

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_kvm import FakeKVM  # noqa: E402
from hid_session import FlowError, HidSession  # noqa: E402
from match_diagnostics import MatchDiagnostics  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402


class ClickKvm(FakeKVM):
    def __init__(self, frame):
        super().__init__(frame)
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FailingClickKvm(ClickKvm):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


def _session(kvm, root):
    return HidSession(kvm, root, MatchDiagnostics(root, "BT"), threshold=0.8, log=lambda *_: None)


def _dock(root):
    catalog = TemplateCatalog(root)
    frame = np.random.RandomState(1).randint(0, 255, (80, 120, 3), dtype=np.uint8)
    catalog.save_crop(frame, (20, 10, 50, 40), "BT", "dock_icon")
    return frame


def test_find_returns_the_box_and_click_hits_its_center(tmp_path):
    root = tmp_path / "template"
    frame = _dock(root)
    kvm = ClickKvm(frame)
    session = _session(kvm, root)
    template = session.load("BT", "dock_icon")

    box = session.find("dock_icon", template, frame, downscale=False)
    asyncio.run(session.click(box, failure="Dock HID 錯誤"))

    assert box.left is not None
    assert kvm.clicks == [(int(box.left + box.width / 2), int(box.top + box.height / 2))]
    assert kvm.rpc_errors == []


def test_find_raises_flow_error_when_the_template_is_absent(tmp_path):
    root = tmp_path / "template"
    frame = _dock(root)
    session = _session(ClickKvm(frame), root)
    template = session.load("BT", "dock_icon")
    blank = np.zeros_like(frame)

    with pytest.raises(FlowError) as excinfo:
        session.find("dock_icon", template, blank, downscale=False)

    assert excinfo.value.code == "hid_failed"
    assert excinfo.value.entered == []


def test_click_raises_flow_error_when_hid_reports_an_error(tmp_path):
    root = tmp_path / "template"
    frame = _dock(root)
    kvm = FailingClickKvm(frame)
    session = _session(kvm, root)
    template = session.load("BT", "dock_icon")
    box = session.find("dock_icon", template, frame, downscale=False)

    with pytest.raises(FlowError) as excinfo:
        asyncio.run(session.click(box, failure="Dock HID 錯誤"))

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value) == "Dock HID 錯誤: hidg endpoint disconnected"
    assert kvm.clicks
