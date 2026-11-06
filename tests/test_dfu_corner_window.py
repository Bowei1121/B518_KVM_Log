"""DFU: the window template covers only a corner; controls outside its width must still be found."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfu_flow  # noqa: E402
from dfu_helpers import SyntheticDfuKVM, save_templates, WINDOW  # noqa: E402
from hid_session import FlowError  # noqa: E402

FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]
CORNER = (WINDOW[0], WINDOW[1], 40, 30)  # x, y, w, h: top-left corner only


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "template"
    catalog = save_templates(root, FOUR)
    kvm = SyntheticDfuKVM(FOUR)
    x, y, w, h = CORNER
    catalog.save_crop(kvm.frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "DFU", "window", overwrite=True)
    return root


def _quiet(*_):
    pass


def test_input_works_when_window_template_is_only_a_corner(root):
    kvm = SyntheticDfuKVM(FOUR)
    result = asyncio.run(dfu_flow.run_dfu_input(kvm, 4, [{"slot": 1, "sn": "A"}, {"slot": 4, "sn": "D"}], root, log=_quiet))
    assert result == {"entered": [1, 4]}
    assert kvm.checked_channels() == [1, 4]
    assert [e for e in kvm.events if e[0] == "type"] == [("type", "A"), ("type", "D")]


def test_ok_works_when_window_template_is_only_a_corner(root):
    kvm = SyntheticDfuKVM(FOUR)
    assert asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=_quiet)) == {"action": "ok"}
    assert [e for e in kvm.events if e[0] == "click"]


def test_extra_checkbox_beyond_window_template_fails_closed(root):
    kvm = SyntheticDfuKVM(FOUR + [(200, 50)])
    with pytest.raises(FlowError) as exc:
        asyncio.run(dfu_flow.run_dfu_input(kvm, 4, [{"slot": 1, "sn": "A"}], root, log=_quiet))
    assert exc.value.code == "hid_failed"
    # Only the Dock focus click may happen; no checkbox click and no SN.
    assert [e for e in kvm.events if e[0] in ("click", "type", "enter")] == [("click", 25, 225)]


def test_search_region_extends_to_frame_edges():
    import numpy as np
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    assert dfu_flow.search_region(frame, (30, 20, 5, 5)) == (30, 20, 170, 80)
