"""Known risk (#168): green cover over 3 of 4 corners reads FAIL as PASS. #172 flips this test."""
import time

from log_solution_reader import (
    MarkerState, RESULT_CELL_HEIGHT, RESULT_CELL_WIDTH, RESULT_FIRST_Y,
    inspect_app_frame,
)
from test_log_solution_reader import render_frame


def test_known_risk_green_cover_reads_as_pass_until_172():
    """Known risk: #172 must flip this test after four-corner and black-border checks."""
    scale, x, y = 2, 23, 31
    frame = render_frame(MarkerState.COMPLETE, ("FAIL",) * 4)
    top = y + RESULT_FIRST_Y * scale
    frame[top:top + RESULT_CELL_HEIGHT * scale,
          x + 2 * RESULT_CELL_WIDTH * scale:x + 4 * RESULT_CELL_WIDTH * scale] = (0, 239, 0)
    now = time.monotonic()
    observation = inspect_app_frame(frame, now, now)
    assert observation.reliable
    assert observation.statuses[2:4] == ("PASS", "PASS")


def test_known_risk_green_over_three_corners_reads_fail_as_pass_until_172():
    """Known risk: green within 20 BGR of PASS over 3 of 4 corner samples wins the 3-vote rule. #172 must flip this."""
    scale, x, y = 2, 23, 31
    frame = render_frame(MarkerState.COMPLETE, ("FAIL",) * 4)
    cell_x, cell_y = x + 1 * RESULT_CELL_WIDTH * scale, y + RESULT_FIRST_Y * scale
    for cx, cy in ((6, 5), (28, 5), (6, 21)):
        px, py = cell_x + cx * scale, cell_y + cy * scale
        frame[py - 6:py + 7, px - 6:px + 7] = (10, 229, 10)
    now = time.monotonic()
    observation = inspect_app_frame(frame, now, now)
    assert observation.reliable
    assert observation.statuses[1] == "PASS"
