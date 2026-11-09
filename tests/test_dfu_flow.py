"""DFU input 1.0 on a synthetic DFU window: checkbox discovery, channel order, SN entry."""

import asyncio
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfu_flow  # noqa: E402
from hid_session import FlowError  # noqa: E402
from dfu_helpers import BOX, INPUT, SyntheticDfuKVM, checkbox_templates, save_templates  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]


@pytest.fixture
def root(tmp_path):
    return tmp_path / "template"


def _run(kvm, channels, items, root):
    return asyncio.run(dfu_flow.run_dfu_input(kvm, channels, items, root, log=lambda *_: None))


# --- parsing -----------------------------------------------------------------

def test_parse_items_sorts_sparse_slots():
    assert dfu_flow.parse_items([{"slot": 3, "sn": "SN3"}, {"slot": 1, "sn": "SN1"}], 4) == [(1, "SN1"), (3, "SN3")]


def test_sn_may_contain_commas_and_colons():
    assert dfu_flow.parse_items([{"slot": 1, "sn": "F5K2:003,B"}], 4) == [(1, "F5K2:003,B")]


@pytest.mark.parametrize("items", [
    [],
    "not-a-list",
    [{"slot": 0, "sn": "A"}],
    [{"slot": 5, "sn": "A"}],
    [{"slot": True, "sn": "A"}],
    [{"slot": "1", "sn": "A"}],
    [{"slot": 1, "sn": ""}],
    [{"slot": 1, "sn": "x" * 65}],
    [{"slot": 1, "sn": "A"}, {"slot": 1, "sn": "B"}],
    [{"slot": 1}],
    [{"slot": 1, "sn": "A", "extra": 1}],
    ["1:A"],
])
def test_invalid_items_are_refused_as_invalid_argument(items):
    with pytest.raises(FlowError) as excinfo:
        dfu_flow.parse_items(items, 4)
    assert excinfo.value.code == "invalid_argument"


# --- checkbox discovery ------------------------------------------------------

def test_checkboxes_are_ordered_top_to_bottom_then_left_to_right(root):
    shuffled = [(120, 90), (40, 50), (120, 50), (40, 90)]
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(shuffled)
    checked, unchecked = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) for image in checkbox_templates())
    boxes = dfu_flow.find_checkboxes(kvm.frame, (10, 10, 240, 190), checked, unchecked)
    centres = [(box["box"][0], box["box"][1]) for box in boxes]
    assert centres == [(40, 50), (120, 50), (40, 90), (120, 90)]


# --- full input flow ----------------------------------------------------------

def test_items_set_only_requested_channels_and_type_in_slot_order(root):
    save_templates(root, FOUR, [True, False, False, True])
    kvm = SyntheticDfuKVM(FOUR, [True, False, False, True])

    result = _run(kvm, 4, [{"slot": 3, "sn": "SN3"}, {"slot": 1, "sn": "SN1"}], root)

    assert result == {"entered": [1, 3]}
    assert kvm.checked_channels() == [1, 3]
    types = [event for event in kvm.events if event[0] == "type"]
    assert types == [("type", "SN1"), ("type", "SN3")]
    first_type = kvm.events.index(types[0])
    assert all(event[0] != "click" or kvm.events.index(event) < first_type
               for event in kvm.events[:first_type])
    assert kvm.events[-1] == ("enter",)
    assert not any(event[0] == "log_shortcut" for event in kvm.events)


def test_a_checkbox_count_other_than_channels_refuses_before_any_sn(root):
    three = FOUR[:3]
    save_templates(root, three)
    kvm = SyntheticDfuKVM(three)

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert excinfo.value.entered == []
    assert not any(event[0] == "type" for event in kvm.events)


def test_a_lost_click_is_corrected_by_one_retry(root):
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    kvm.lost_clicks = [1]  # the first click on channel 2 is ignored

    result = _run(kvm, 4, [{"slot": 2, "sn": "SN2"}], root)

    assert result == {"entered": [2]}
    assert kvm.checked_channels() == [2]


def test_a_second_mismatch_stops_before_typing(root):
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    kvm.lost_clicks = [1, 1]  # the click and its retry both fail

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 2, "sn": "SN2"}], root)

    assert excinfo.value.code == "hid_failed"
    assert not any(event[0] == "type" for event in kvm.events)


def test_hid_error_while_typing_reports_the_slots_already_entered(root):
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    kvm.hid_error_after_types = 1

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}, {"slot": 2, "sn": "SN2"}], root)

    assert excinfo.value.code == "hid_failed"
    assert excinfo.value.entered == [1]


class _CheckboxHid(SyntheticDfuKVM):
    """Each checkbox click reports a different HID error. The dock click does not."""

    def __init__(self, positions):
        super().__init__(positions)
        self._errors = iter(("err1", "err2"))

    async def click(self, x, y):
        await super().click(x, y)
        for px, py in self.positions:
            if px <= x < px + BOX and py <= y < py + BOX:
                self.rpc_errors.append(next(self._errors))
                return


def test_checkbox_hid_stops_at_the_first_error(root):
    save_templates(root, FOUR)
    kvm = _CheckboxHid(FOUR)

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}, {"slot": 2, "sn": "SN2"}], root)

    assert str(excinfo.value) == "Checkbox HID failed: err1"
    assert excinfo.value.entered == []
    assert sum(event[0] == "click" for event in kvm.events) == 2
    assert not any(event[0] == "type" for event in kvm.events)


class _InputClickHid(SyntheticDfuKVM):
    async def click(self, x, y):
        await super().click(x, y)
        ix, iy, iw, ih = INPUT
        if ix <= x < ix + iw and iy <= y < iy + ih:
            self.rpc_errors.append("input lost")


def test_input_click_hid_error_types_nothing(root):
    save_templates(root, FOUR, [True, False, False, False])
    kvm = _InputClickHid(FOUR, [True, False, False, False])

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert str(excinfo.value) == "SN input HID failed: input lost"
    assert excinfo.value.entered == []
    assert not any(event[0] == "type" for event in kvm.events)


def test_missing_template_is_hid_failed_before_any_click(root):
    save_templates(root, FOUR)
    (root / "DFU" / "DFU_checkbox_unchecked.png").unlink()
    kvm = SyntheticDfuKVM(FOUR)

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert kvm.events == []


# --- extra ATE slots (7-slot DFU window, 4-channel round) ----------------------

SEVEN = [(40, 50), (100, 50), (160, 50), (220, 50), (40, 90), (100, 90), (160, 90)]


def _narrow_region(root):
    import json
    (root / "DFU" / "DFU_region.json").write_text(json.dumps({"x": 10, "y": 10, "width": 240, "height": 190}))


def test_extra_ate_slots_are_kept_unchecked_inside_a_narrowed_region(root):
    initial = [False, False, False, False, True, True, True]
    save_templates(root, SEVEN, initial)
    _narrow_region(root)
    kvm = SyntheticDfuKVM(SEVEN, initial)

    items = [{"slot": slot, "sn": f"SN{slot}"} for slot in range(1, 5)]
    result = _run(kvm, 4, items, root)

    assert result == {"entered": [1, 2, 3, 4]}
    assert kvm.checked_channels() == [1, 2, 3, 4]


def test_extra_ate_slots_are_refused_with_the_full_screen_default_region(root):
    save_templates(root, SEVEN)
    kvm = SyntheticDfuKVM(SEVEN)

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert "DFU_region.json" in str(excinfo.value)
    assert not any(event[0] == "type" for event in kvm.events)


def test_fewer_boxes_than_channels_is_refused_even_inside_a_narrowed_region(root):
    save_templates(root, FOUR[:3])
    _narrow_region(root)
    kvm = SyntheticDfuKVM(FOUR[:3])

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert not any(event[0] == "type" for event in kvm.events)


# --- blue-fill fallback for the macOS checked state ----------------------------

def _blue_box(canvas, x, y, side=BOX, width=None):
    canvas[y:y + side, x:x + (width or side)] = (250, 120, 30)  # BGR accent blue
    canvas[y + side // 2, x + 3:x + (width or side) - 3] = (255, 255, 255)  # tick stand-in


def test_a_blue_checked_box_is_found_when_the_checked_template_misses():
    frame = np.full((120, 200, 3), 200, dtype=np.uint8)
    _blue_box(frame, 20, 20)
    _, unchecked = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) for image in checkbox_templates())
    useless_checked = np.random.RandomState(0).randint(0, 255, (BOX, BOX), dtype=np.uint8)

    boxes = dfu_flow.find_checkboxes(frame, (0, 0, 200, 120), useless_checked, unchecked)

    assert [(box["state"], box["box"]) for box in boxes] == [("checked", (20, 20, BOX, BOX))]


def test_wide_blue_icons_are_not_mistaken_for_checked_boxes():
    frame = np.full((120, 200, 3), 200, dtype=np.uint8)
    _blue_box(frame, 20, 20, side=BOX, width=int(BOX * 1.4))  # folder-icon shape
    _blue_box(frame, 80, 20, side=3)  # too small
    _, unchecked = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) for image in checkbox_templates())
    useless_checked = np.random.RandomState(0).randint(0, 255, (BOX, BOX), dtype=np.uint8)

    assert dfu_flow.find_checkboxes(frame, (0, 0, 200, 120), useless_checked, unchecked) == []


@pytest.mark.parametrize("region", [(0, 0, 1921, 1080), (0, 0, 20000, 20000),
                                   (0, 0, 1920, 1080), (0, 0, 1919, 1080)])
def test_extra_boxes_permission_uses_clipped_region_and_actual_frame(region):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert dfu_flow._allows_extra_boxes(frame, dfu_flow._clip(frame, region)) == (region[2] < 1920)


@pytest.mark.parametrize("width,height", [(1921, 1080), (20000, 20000)])
def test_oversized_full_frame_region_requires_exact_count(root, width, height):
    import json
    save_templates(root, SEVEN)
    (root / "DFU" / "DFU_region.json").write_text(json.dumps(
        {"x": 0, "y": 0, "width": width, "height": height}))
    kvm = SyntheticDfuKVM(SEVEN)

    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert not any(event[0] == "type" for event in kvm.events)


@pytest.mark.parametrize("change", ["missing", "inserted", "reordered", "second_verify"])
def test_changed_channel_identity_stops_before_correction_or_typing(root, monkeypatch, change):
    save_templates(root, SEVEN)
    _narrow_region(root)
    kvm = SyntheticDfuKVM(SEVEN)
    if change == "second_verify":
        kvm.lost_clicks = [0]
    find = dfu_flow.find_checkboxes
    calls = 0
    events_at_change = []

    def changed_boxes(*args, **kwargs):
        nonlocal calls
        calls += 1
        boxes = find(*args, **kwargs)
        if calls == (3 if change == "second_verify" else 2):
            events_at_change.extend(kvm.events)
            if change in ("missing", "second_verify"):
                return boxes[1:]  # 7 -> 6 still passes the old >= 4 count check.
            if change == "inserted":
                return [{"state": "unchecked", "box": (20, 30, BOX, BOX)}] + boxes
            return [boxes[1], boxes[0]] + boxes[2:]
        return boxes

    monkeypatch.setattr(dfu_flow, "find_checkboxes", changed_boxes)
    with pytest.raises(FlowError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert str(excinfo.value).isascii()
    assert "identity changed" in str(excinfo.value)
    assert kvm.events == events_at_change  # No correction click can remap a channel.
    assert not any(event[0] == "type" for event in kvm.events)


def test_locked_slots_allow_small_jitter_and_changes_to_trailing_extras():
    slots = [(x, y, BOX, BOX) for x, y in SEVEN[:4]]
    boxes = [{"box": (x + 2, y - 2, w, h)} for x, y, w, h in slots]
    boxes.append({"box": (100, 100, BOX, BOX)})
    dfu_flow._verify_slots(slots, boxes)


def test_locked_slot_must_have_a_unique_position_match():
    slot = (40, 50, BOX, BOX)
    with pytest.raises(FlowError, match="identity changed"):
        dfu_flow._verify_slots([slot], [{"box": slot}, {"box": slot}])
