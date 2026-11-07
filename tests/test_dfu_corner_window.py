"""DFU: a fixed screen region (not the window template, whose background varies) bounds every control search."""

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


def test_missing_region_file_is_created_full_screen_and_flow_runs(tmp_path):
    root = _make_root(tmp_path, region=None)
    path = root / "DFU" / dfu_flow.REGION_FILE
    assert not path.exists()
    kvm = SyntheticDfuKVM(FOUR)
    assert _input(kvm, root, [{"slot": 2, "sn": "B"}]) == {"entered": [2]}
    assert json.loads(path.read_text(encoding="utf-8")) == dfu_flow.DEFAULT_REGION


def test_existing_region_file_is_not_overwritten(tmp_path):
    root = _make_root(tmp_path)
    path = root / "DFU" / dfu_flow.REGION_FILE
    before = path.read_text(encoding="utf-8")
    _input(SyntheticDfuKVM(FOUR), root, [{"slot": 1, "sn": "A"}])
    assert path.read_text(encoding="utf-8") == before


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


@pytest.mark.parametrize("region", [(10, 10, 20, 1), (10, 10, 2, 2), (10, 10, 240, 5), (10, 10, 5, 190), (235, 250, 100, 100)])
def test_region_too_small_for_a_checkbox_is_hid_failed_without_clicks(tmp_path, region):
    root = _make_root(tmp_path, region=region)
    kvm = SyntheticDfuKVM(FOUR)
    with pytest.raises(FlowError) as exc:
        _input(kvm, root, [{"slot": 1, "sn": "A"}])
    assert exc.value.code == "hid_failed"
    assert "cv2" not in type(exc.value).__module__
    assert _hid(kvm) == [DOCK_CLICK]


def test_find_checkboxes_rejects_a_two_dimension_too_small_area():
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
    a = rng.integers(0, 256, (14, 14), dtype=np.uint8)
    b = rng.integers(0, 256, (14, 14), dtype=np.uint8)
    with pytest.raises(FlowError):
        dfu_flow.find_checkboxes(frame, (10, 10, 2, 2), b, a)
