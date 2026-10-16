"""DFU input 1.0 on a synthetic DFU window: checkbox discovery, channel order, SN entry."""

import asyncio
import sys
import tempfile
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfu_flow  # noqa: E402
from dfu_flow import DfuInputError  # noqa: E402
from dfu_helpers import SyntheticDfuKVM, checkbox_templates, save_templates  # noqa: E402

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
    with pytest.raises(DfuInputError) as excinfo:
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

    with pytest.raises(DfuInputError) as excinfo:
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

    with pytest.raises(DfuInputError) as excinfo:
        _run(kvm, 4, [{"slot": 2, "sn": "SN2"}], root)

    assert excinfo.value.code == "hid_failed"
    assert not any(event[0] == "type" for event in kvm.events)


def test_hid_error_while_typing_reports_the_slots_already_entered(root):
    save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    kvm.hid_error_after_types = 1

    with pytest.raises(DfuInputError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}, {"slot": 2, "sn": "SN2"}], root)

    assert excinfo.value.code == "hid_failed"
    assert excinfo.value.entered == [1]


def test_missing_template_is_hid_failed_before_any_click(root):
    save_templates(root, FOUR)
    (root / "DFU" / "DFU_checkbox_unchecked.png").unlink()
    kvm = SyntheticDfuKVM(FOUR)

    with pytest.raises(DfuInputError) as excinfo:
        _run(kvm, 4, [{"slot": 1, "sn": "SN1"}], root)

    assert excinfo.value.code == "hid_failed"
    assert kvm.events == []
