"""DFU: a corner-only window template plus a trusted window size; controls outside that area are never used."""

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfu_flow  # noqa: E402
from dfu_helpers import BUTTON, SyntheticDfuKVM, WINDOW, save_templates  # noqa: E402
from hid_session import FlowError  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
CORNER = (WINDOW[0], WINDOW[1], 40, 30)  # x, y, w, h: top-left corner only
DOCK_CLICK = ("click", 25, 225)


def _quiet(*_):
    pass


def _make_root(tmp_path, size=(WINDOW[2], WINDOW[3])):
    root = tmp_path / "template"
    catalog = save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    x, y, w, h = CORNER
    catalog.save_crop(kvm.frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "DFU", "window", overwrite=True)
    if size is not None:
        (catalog.device_dir("DFU") / dfu_flow.EXTENT_FILE).write_text(
            json.dumps({"width": size[0], "height": size[1]}), encoding="utf-8")
    return root


def _input(kvm, root, items):
    return asyncio.run(dfu_flow.run_dfu_input(kvm, 4, items, root, log=_quiet))


def _hid(kvm):
    return [e for e in kvm.events if e[0] in ("click", "type", "enter")]


def test_input_works_with_corner_template_and_window_size(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    assert _input(kvm, root, [{"slot": 1, "sn": "A"}, {"slot": 4, "sn": "D"}]) == {"entered": [1, 4]}
    assert kvm.checked_channels() == [1, 4]
    assert [e for e in kvm.events if e[0] == "type"] == [("type", "A"), ("type", "D")]


def test_ok_works_with_corner_template_and_window_size(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    assert asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=_quiet)) == {"action": "ok"}
    bx, by, bw, bh = BUTTON
    assert _hid(kvm)[-1] == ("click", bx + bw // 2, by + bh // 2)


def test_without_window_size_a_corner_template_stays_strict(tmp_path):
    root = _make_root(tmp_path, size=None)
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == [DOCK_CLICK]


def test_checkbox_outside_window_cannot_fill_a_missing_channel(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR[:3] + [(180, 205)])  # 4th checkbox is outside the 240x190 window
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 4, "sn": "SLOT4"}])
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == [DOCK_CLICK]


def test_ok_button_outside_window_is_never_clicked(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    bx, by, bw, bh = BUTTON
    kvm.button_patch_screen = None
    original = kvm._render

    def render():
        canvas = original()
        canvas[by:by + bh, bx:bx + bw] = 200  # real OK gone
        canvas[0:bh, 0:bw] = kvm.button_patch  # same pattern in another window
        return canvas

    kvm._render = render
    kvm.frame = render()
    with pytest.raises(FlowError) as exc:
        asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=_quiet))
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == [DOCK_CLICK]


def test_input_field_outside_window_is_never_clicked(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    ix, iy, iw, ih = (30, 150, 80, 16)
    original = kvm._render

    def render():
        canvas = original()
        canvas[iy:iy + ih, ix:ix + iw] = 200
        canvas[0:ih, 0:iw] = kvm.input_patch
        return canvas

    kvm._render = render
    kvm.frame = render()
    with pytest.raises(FlowError):
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert not [e for e in kvm.events if e[0] in ("type", "enter")]


@pytest.mark.parametrize("content", ["not json", '{"width": 0, "height": 5}', '{"width": "9", "height": 5}', "{}"])
def test_invalid_window_size_file_is_hid_failed(tmp_path, content):
    root = _make_root(tmp_path, size=None)
    (root / "DFU" / dfu_flow.EXTENT_FILE).write_text(content, encoding="utf-8")
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert not [e for e in kvm.events if e[0] in ("type", "enter")]


def test_window_region_scales_and_clips():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    template = np.zeros((10, 20, 3), dtype=np.uint8)
    # match at 2x scale: 40 wide -> size scaled by 2, clipped to the frame edge
    assert dfu_flow.window_region(frame, (150, 20, 40, 20), template, (60, 30)) == (150, 20, 50, 60)
    assert dfu_flow.window_region(frame, (30, 20, 20, 10), template, None) == (30, 20, 20, 10)
