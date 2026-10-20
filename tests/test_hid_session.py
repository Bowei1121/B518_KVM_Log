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

    assert tuple(box) == (20, 10, 30, 30)
    assert kvm.clicks == [(35, 25)]
    assert kvm.rpc_errors == []


def test_find_within_reports_frame_coordinates(tmp_path):
    root = tmp_path / "template"
    catalog = TemplateCatalog(root)
    frame = np.random.RandomState(2).randint(0, 255, (80, 120, 3), dtype=np.uint8)
    catalog.save_crop(frame, (40, 20, 70, 50), "BT", "dock_icon")
    session = _session(ClickKvm(frame), root)
    template = session.load("BT", "dock_icon")

    box = session.find("dock_icon", template, frame, within=(30, 10, 60, 50), downscale=False)

    assert tuple(box) == (40, 20, 30, 30)


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
    assert str(excinfo.value).startswith("找不到 dock_icon（相似度")
    assert excinfo.value.score is not None


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
    assert kvm.clicks == [(35, 25)]


def test_click_raises_before_clearing_a_previous_hid_error(tmp_path):
    root = tmp_path / "template"
    frame = _dock(root)
    kvm = ClickKvm(frame)
    kvm.rpc_errors.append("late from previous step")
    session = _session(kvm, root)
    template = session.load("BT", "dock_icon")
    box = session.find("dock_icon", template, frame, downscale=False)

    with pytest.raises(FlowError) as excinfo:
        asyncio.run(session.click(box, failure="Dock HID 錯誤"))

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value) == "Dock HID 錯誤: late from previous step"
    assert kvm.clicks == []
    assert kvm.rpc_errors == ["late from previous step"]


def test_type_text_raises_when_hid_reports_an_error(tmp_path):
    root = tmp_path / "template"
    kvm = ClickKvm(_dock(root))
    session = _session(kvm, root)

    async def type_then_fail(text):
        kvm.rpc_errors.append("keyboard lost")

    kvm.type_text = type_then_fail

    with pytest.raises(FlowError) as excinfo:
        asyncio.run(session.type_text("SN1", failure="SN 輸入 HID 錯誤", entered=[1]))

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value) == "SN 輸入 HID 錯誤: keyboard lost"
    assert excinfo.value.entered == [1]


def test_press_enter_raises_when_hid_reports_an_error(tmp_path):
    root = tmp_path / "template"
    kvm = ClickKvm(_dock(root))
    session = _session(kvm, root)

    async def press_then_fail():
        kvm.rpc_errors.append("keyboard lost")

    kvm.press_enter = press_then_fail

    with pytest.raises(FlowError) as excinfo:
        asyncio.run(session.press_enter(failure="SN 輸入 HID 錯誤", entered=[1]))

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value) == "SN 輸入 HID 錯誤: keyboard lost"
    assert excinfo.value.entered == [1]


def test_press_command_shift_m_raises_when_hid_reports_an_error(tmp_path):
    root = tmp_path / "template"
    kvm = ClickKvm(_dock(root))
    session = _session(kvm, root)

    async def shortcut_then_fail():
        kvm.rpc_errors.append("shortcut lost")

    kvm.press_command_shift_m = shortcut_then_fail

    with pytest.raises(FlowError) as excinfo:
        asyncio.run(session.press_command_shift_m(failure="快捷鍵 HID 錯誤"))

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value) == "快捷鍵 HID 錯誤: shortcut lost"
    assert excinfo.value.entered == []


def test_find_within_raises_when_the_template_is_only_outside_the_roi(tmp_path):
    root = tmp_path / "template"
    frame = _dock(root)
    session = _session(ClickKvm(frame), root)
    template = session.load("BT", "dock_icon")

    with pytest.raises(FlowError) as excinfo:
        session.find("dock_icon", template, frame, within=(80, 50, 40, 30), downscale=False)

    assert excinfo.value.code == "hid_failed"
    assert excinfo.value.score is not None
