"""DFU: a fixed screen region (not the window template, whose background varies) bounds every control search."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfu_flow  # noqa: E402
from dfu_helpers import BUTTON, SyntheticDfuKVM, WINDOW, save_templates  # noqa: E402
from hid_session import FlowError  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
DOCK_CLICK = ("click", 25, 225)


def _quiet(*_):
    pass


def _make_root(tmp_path, region=WINDOW, with_window_template=False):
    """Templates without a usable window template; DFU_region.json fixes where the DFU window is."""
    root = tmp_path / "template"
    catalog = save_templates(root, FOUR)
    catalog.path("DFU", "window").unlink()
    if with_window_template:
        kvm = SyntheticDfuKVM(FOUR)
        x, y, w, h = WINDOW
        catalog.save_crop(kvm.frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "DFU", "window")
    if region is not None:
        x, y, w, h = region
        (catalog.device_dir("DFU") / dfu_flow.REGION_FILE).write_text(
            json.dumps({"x": x, "y": y, "width": w, "height": h}), encoding="utf-8")
    return root


def _input(kvm, root, items):
    return asyncio.run(dfu_flow.run_dfu_input(kvm, 4, items, root, log=_quiet))


def _hid(kvm):
    return [e for e in kvm.events if e[0] in ("click", "type", "enter")]


def test_input_works_from_fixed_region_without_window_template(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    assert _input(kvm, root, [{"slot": 1, "sn": "A"}, {"slot": 4, "sn": "D"}]) == {"entered": [1, 4]}
    assert kvm.checked_channels() == [1, 4]
    assert [e for e in kvm.events if e[0] == "type"] == [("type", "A"), ("type", "D")]


def test_ok_works_from_fixed_region_without_window_template(tmp_path):
    root = _make_root(tmp_path)
    kvm = SyntheticDfuKVM(FOUR)
    assert asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=_quiet)) == {"action": "ok"}
    bx, by, bw, bh = BUTTON
    assert _hid(kvm)[-1] == ("click", bx + bw // 2, by + bh // 2)


def test_without_region_file_the_window_template_is_required(tmp_path):
    root = _make_root(tmp_path, region=None)
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == []


def test_without_region_file_the_window_template_still_works(tmp_path):
    root = _make_root(tmp_path, region=None, with_window_template=True)
    kvm = SyntheticDfuKVM(FOUR)
    assert _input(kvm, root, [{"slot": 2, "sn": "B"}]) == {"entered": [2]}


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


@pytest.mark.parametrize("content", ["not json", '{"x": 0, "y": 0, "width": 0, "height": 5}',
                                     '{"x": "1", "y": 0, "width": 5, "height": 5}', '{"x": 1}'])
def test_invalid_region_file_is_hid_failed(tmp_path, content):
    root = _make_root(tmp_path, region=None)
    (root / "DFU" / dfu_flow.REGION_FILE).write_text(content, encoding="utf-8")
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == []


def test_region_outside_the_frame_is_hid_failed(tmp_path):
    root = _make_root(tmp_path, region=(5000, 5000, 100, 100))
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert _hid(kvm) == [DOCK_CLICK]
