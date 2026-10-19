"""log_solution_reader without a socket: what one reading (or two fresh readings) decides."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from frame_snapshot import FrameSnapshot  # noqa: E402
from log_solution_reader import (  # noqa: E402
    ActivityTracker, FrameObservation, MarkerState, inspect_app_frame, read_snapshot,
    reportable_active, screen_kind,
)

COLORS = {
    "PASS": (0, 239, 0), "FAIL": (0, 0, 255), "TESTING": (0, 255, 255),
    "NOTEST": (240, 75, 240), "WAITING": (217, 217, 217),
    "COMPLETING": (255, 199, 130), "STOPPED": (191, 191, 191),
    "TIMEOUT": (0, 153, 255),
}
PATTERNS = {
    MarkerState.STANDBY: (1, 0, 0, 1),
    MarkerState.PREPARING: (0, 1, 0, 1),
    MarkerState.MONITORING: (1, 1, 0, 0),
    MarkerState.REVIEW: (1, 0, 1, 0),
    MarkerState.COMPLETE: (0, 1, 1, 0),
    MarkerState.MANUAL_STOP_COMPLETE: (0, 0, 1, 1),
}


def render_frame(state, statuses, scale=2, origin=(23, 31)):
    """Contract 1.1 layout, the same geometry the reader samples."""
    width, height = round(376 * scale), round(110 * scale)
    ox, oy = origin
    frame = np.full((height + oy, width + ox, 3), (245, 246, 247), dtype=np.uint8)

    def rect(x, y, w, h, color):
        x0, y0 = round(ox + x * scale), round(oy + y * scale)
        x1, y1 = round(ox + (x + w) * scale), round(oy + (y + h) * scale)
        frame[y0:y1, x0:x1] = color

    for x, inset in ((82, 3), (320, 11)):
        rect(x, 2, 22, 22, (0, 0, 0))
        rect(x + inset, 2 + inset, 8, 8, (255, 255, 255))
    for index, black in enumerate(PATTERNS[state]):
        row, col = divmod(index, 2)
        rect(278 + 2 + col * 12, 2 + row * 12, 10, 10, (0, 0, 0) if black else (255, 255, 255))
    rows = 2 if len(statuses) > 10 else 1
    rect(130, 2, 26, 14, (255, 255, 255))
    for index, black in enumerate((rows == 1, rows == 2)):
        rect(132 + index * 12, 4, 10, 10, (0, 0, 0) if black else (255, 255, 255))
    for slot in range(1, rows * 10 + 1):
        row, col = divmod(slot - 1, 10)
        status = statuses[slot - 1] if slot <= len(statuses) else "OUTSIDE"
        rect(col * 34, 34 + row * 27, 34, 26, (0, 0, 0) if status == "OUTSIDE" else COLORS[status])
    return frame


def reading(state, statuses):
    return inspect_app_frame(render_frame(state, statuses), 10, 10)


def unreadable():
    return inspect_app_frame(np.zeros((120, 400, 3), dtype=np.uint8), 10, 10)


def test_reading_reports_state_capacity_and_statuses():
    obs = reading(MarkerState.MONITORING, ["TESTING", "WAITING"])

    assert obs.reliable
    assert obs.state == MarkerState.MONITORING
    assert obs.capacity == 2
    assert obs.statuses == ("TESTING", "WAITING")


@pytest.mark.parametrize("state", [MarkerState.PREPARING, MarkerState.MONITORING, MarkerState.REVIEW])
def test_preparing_monitoring_and_review_are_busy_and_block_hid(state):
    obs = reading(state, ["WAITING"] * 4)

    assert obs.busy
    assert not obs.may_open_round
    assert not obs.may_send_hid
    assert not obs.finished


@pytest.mark.parametrize("state", [MarkerState.STANDBY, MarkerState.COMPLETE, MarkerState.MANUAL_STOP_COMPLETE])
def test_standby_and_finished_screens_may_open_a_round(state):
    obs = reading(state, ["PASS", "FAIL", "NOTEST", "TIMEOUT"])

    assert not obs.busy
    assert obs.may_open_round
    assert obs.may_send_hid
    assert obs.finished == (state != MarkerState.STANDBY)


def test_only_complete_screens_with_terminal_results_are_results_complete():
    assert reading(MarkerState.COMPLETE, ["PASS"] * 4).results_complete
    assert not reading(MarkerState.STANDBY, ["PASS"] * 4).results_complete
    assert not reading(MarkerState.COMPLETE, ["TESTING"] * 4).results_complete


def test_unreadable_screen_is_not_busy_so_the_dock_click_stays_allowed():
    obs = unreadable()

    assert not obs.reliable
    assert not obs.busy
    assert not obs.finished
    assert not obs.may_open_round
    assert obs.may_send_hid


def test_screen_kind_maps_one_reading_to_unknown_paused_or_waiting():
    assert screen_kind(unreadable()) == "unknown"
    assert screen_kind(reading(MarkerState.REVIEW, ["PASS", "FAIL"])) == "paused"
    assert screen_kind(reading(MarkerState.MONITORING, ["WAITING"])) == "waiting"
    assert screen_kind(reading(MarkerState.COMPLETE, ["PASS"])) == "waiting"


def test_finished_screen_never_reports_started_channels():
    rows = ((1, "testing"), (2, "result"))

    assert reportable_active(MarkerState.COMPLETE, rows) == ()
    assert reportable_active(MarkerState.MANUAL_STOP_COMPLETE, rows) == ()
    assert reportable_active(MarkerState.MONITORING, rows) == rows
    assert reportable_active(None, rows) == rows


def test_two_fresh_frames_confirm_started_channels_with_evidence():
    tracker = ActivityTracker()
    frame = reading(MarkerState.MONITORING, ["TESTING", "PASS", "WAITING"])

    assert tracker.observe(frame, 1, 1.0) == ()
    assert tracker.observe(frame, 2, 1.1) == ((1, "testing"), (2, "result"))


def test_testing_on_either_frame_wins_over_result_evidence():
    tracker = ActivityTracker()
    tracker.observe(reading(MarkerState.MONITORING, ["PASS"]), 1, 1.0)

    assert tracker.observe(reading(MarkerState.MONITORING, ["TESTING"]), 2, 1.1) == ((1, "testing"),)


def test_unreliable_frame_breaks_the_pair_and_a_repeated_sequence_does_not():
    tracker = ActivityTracker()
    good = reading(MarkerState.MONITORING, ["TESTING"])
    tracker.observe(good, 1, 1.0)

    assert tracker.observe(good, 1, 1.05) == ()
    assert tracker.previous[0] == 1
    assert tracker.observe(good, 2, 1.1) == ((1, "testing"),)

    tracker.observe(unreadable(), 3, 1.2)
    assert tracker.previous is None
    assert tracker.observe(good, 4, 1.3) == ()


def test_stale_gap_between_frames_breaks_the_pair():
    tracker = ActivityTracker(max_gap=1.0)
    good = reading(MarkerState.MONITORING, ["TESTING"])
    tracker.observe(good, 1, 1.0)

    assert tracker.observe(good, 2, 3.0) == ()


def test_clear_active_keeps_the_pair_waiting_for_the_next_frame():
    tracker = ActivityTracker()
    good = reading(MarkerState.MONITORING, ["TESTING"])
    tracker.observe(good, 1, 1.0)
    tracker.observe(good, 2, 1.1)

    tracker.clear_active()
    assert tracker.last_active == ()
    assert tracker.observe(good, 3, 1.2) == ((1, "testing"),)


def test_reset_forgets_the_pair_and_the_last_answer():
    tracker = ActivityTracker()
    good = reading(MarkerState.MONITORING, ["TESTING"])
    tracker.observe(good, 1, 1.0)
    tracker.observe(good, 2, 1.1)

    tracker.reset()
    assert tracker.previous is None
    assert tracker.last_active == ()


def test_two_trackers_do_not_share_their_pair():
    inspect_tracker = ActivityTracker()
    check_tracker = ActivityTracker()
    good = reading(MarkerState.MONITORING, ["TESTING"])

    inspect_tracker.observe(good, 1, 1.0)
    check_tracker.observe(good, 1, 1.0)
    assert inspect_tracker.observe(good, 2, 1.1) == ((1, "testing"),)
    check_tracker.reset()
    assert check_tracker.previous is None
    assert inspect_tracker.previous is not None


def test_read_snapshot_reads_the_snapshot_fields_and_refuses_stale_pictures():
    image = render_frame(MarkerState.STANDBY, ["WAITING"] * 4)
    snapshot = FrameSnapshot(image=image, sequence=7, received_monotonic=9.9, stream_id="s", pts=None)

    fresh = read_snapshot(snapshot, now=10.0)
    assert fresh.reliable and fresh.state == MarkerState.STANDBY and fresh.capacity == 4

    stale = read_snapshot(FrameSnapshot(image=image, sequence=8, received_monotonic=1.0, stream_id="s", pts=None),
                          now=10.0)
    assert stale.reason == "stale_frame"


def test_read_snapshot_without_a_picture_is_none():
    assert read_snapshot(None, now=10.0) is None


def test_observation_defaults_are_unreadable_and_not_busy():
    obs = FrameObservation(False)

    assert not obs.busy and not obs.finished and not obs.may_open_round
    assert obs.may_send_hid
