"""Field fixes for the 1920x1080 / 100 % BT line (2026-11-06).

1. The BT window template is only the title-bar corner; Start All sits far right of it, at the bottom
   centre of the window. Start All must still be found.
2. At 1 px per unit the reader samples single pixels in the deep ring; an H.264 block shift turns
   WAITING (217) into STOPPED (191). That shift must not make the whole screen unreliable, while a
   differently coloured cover still must.
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from bt_flow import run_start_all  # noqa: E402
from fake_kvm import FakeKVM  # noqa: E402
from log_solution_reader import RESULT_CELL_WIDTH, RESULT_FIRST_Y, inspect_app_frame  # noqa: E402
from protocol_helpers import render_log_solution  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

WINDOW_CORNER = (15, 20, 55, 50)  # x1, y1, x2, y2: traffic-light corner only
START_ALL = (220, 130, 300, 160)  # far right of the corner's width
DOCK = (340, 15, 380, 55)


class ClickKvm(FakeKVM):
    def __init__(self, frame):
        super().__init__(frame)
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


def test_start_all_outside_the_window_corner_width_is_found(tmp_path):
    frame = np.random.RandomState(7).randint(0, 255, (200, 400, 3), dtype=np.uint8)
    catalog = TemplateCatalog(tmp_path / "template")
    catalog.save_crop(frame, WINDOW_CORNER, "BT", "window")
    catalog.save_crop(frame, DOCK, "BT", "dock_icon")
    catalog.save_crop(frame, START_ALL, "BT", "start_all")
    kvm = ClickKvm(frame)

    result = asyncio.run(run_start_all(kvm, template_root=catalog.root, log=lambda *_: None))

    assert result == {"action": "start_all"}
    x, y = kvm.clicks[-1]
    assert START_ALL[0] <= x < START_ALL[2] and START_ALL[1] <= y < START_ALL[3]


def _scale1_standby():
    return render_log_solution("standby", ["WAITING"] * 4, scale=1, origin=(40, 40), heartbeat=5)


def _deep_ring_pixel(slot, colour):
    frame = _scale1_standby()
    x = 40 + (slot - 1) * RESULT_CELL_WIDTH + 17
    y = 40 + RESULT_FIRST_Y + 3
    frame[y, x] = colour
    return frame


def test_scale_1_reads_with_an_h264_grey_shift_in_the_deep_ring():
    assert inspect_app_frame(_scale1_standby(), time.monotonic()).reliable
    shifted = _deep_ring_pixel(2, (199, 204, 200))  # WAITING after a block shift: nearest STOPPED

    observation = inspect_app_frame(shifted, time.monotonic())

    assert observation.reliable, observation.reason
    assert observation.statuses == ("WAITING",) * 4


def test_scale_1_still_rejects_a_differently_coloured_deep_ring():
    covered = _deep_ring_pixel(2, (241, 75, 240))  # NOTEST purple on a WAITING cell

    observation = inspect_app_frame(covered, time.monotonic())

    assert not observation.reliable
    assert observation.reason == "result_cell_unknown_2"


def test_scale_1_still_rejects_an_exact_stopped_cover_on_waiting():
    covered = _deep_ring_pixel(2, (191, 191, 191))

    assert not inspect_app_frame(covered, time.monotonic()).reliable
