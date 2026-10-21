"""Log Solution heartbeat watch (issue #77, C8): the pure rule, the HID probe, and the reading gate.

Clocks are fake: every observation carries its own received time. The heartbeat is drawn with contract 1.3
(16 states, one step per 0.5 s), so a healthy App changes its four cells at every counted observation.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from log_solution_heartbeat import (  # noqa: E402
    HEARTBEAT_STOPPED,
    HEARTBEAT_UNREADABLE,
    PROBE_MAX_SEC,
    HeartbeatTracker,
    Step,
    cells_of,
    probe,
    unreadable_heartbeat,
)
from log_solution_reader import FrameObservation  # noqa: E402
from protocol_helpers import heartbeat_bits, render_log_solution  # noqa: E402
from round_frame_consumer import RoundFrameGate  # noqa: E402
from frame_snapshot import FrameSnapshot  # noqa: E402

STREAM = "stream-a"


def healthy_cells(t, step=0.5):
    """Cells of a live App at time ``t``: one 16-state step per 0.5 s."""
    return heartbeat_bits(int(t / step) % 16)


def feed(tracker, times, cells_at, sequence_at=None, stream_id=STREAM):
    """Feed one observation per time; sequence grows unless ``sequence_at`` says otherwise."""
    steps = []
    for index, t in enumerate(times):
        sequence = index + 1 if sequence_at is None else sequence_at(index)
        steps.append(tracker.observe(stream_id, sequence, t, cells_at(t)))
    return steps


FROZEN_CELLS = (True, False, False, True)


# --- the counting rule -------------------------------------------------------------------------------

@pytest.mark.parametrize("interval", [0.5, 1.0, 2.0])
def test_a_live_heartbeat_sampled_every_half_second_one_or_two_seconds_never_reports_stopped(interval):
    tracker = HeartbeatTracker()
    times = [index * interval for index in range(40)]

    steps = feed(tracker, times, healthy_cells)

    assert Step.STOPPED not in steps
    assert steps.count(Step.CHANGED) > 0


def test_three_identical_counted_readings_in_a_row_report_stopped():
    tracker = HeartbeatTracker()

    steps = feed(tracker, [0.0, 1.0, 2.0], lambda t: FROZEN_CELLS)

    assert steps == [Step.COUNTED, Step.COUNTED, Step.STOPPED]


def test_a_reading_under_point_eight_seconds_after_the_last_counted_one_is_not_counted():
    tracker = HeartbeatTracker()

    steps = feed(tracker, [0.0, 0.3, 0.6, 0.9, 1.2], lambda t: FROZEN_CELLS)

    assert steps == [Step.COUNTED, Step.NOT_COUNTED, Step.NOT_COUNTED, Step.COUNTED, Step.NOT_COUNTED]
    assert Step.STOPPED not in steps


def test_a_repeated_frame_is_neither_counted_nor_clears_the_run():
    tracker = HeartbeatTracker()

    steps = feed(tracker, [0.0, 1.0, 2.0], lambda t: FROZEN_CELLS, sequence_at=lambda index: 7)

    assert steps == [Step.COUNTED, Step.NOT_COUNTED, Step.NOT_COUNTED]


def test_a_gap_over_three_seconds_restarts_the_run():
    tracker = HeartbeatTracker()

    # The third identical reading overall (4.5) starts a new run after the gap, so it is not stopped yet.
    steps = feed(tracker, [0.0, 1.0, 4.5, 5.5, 6.5], lambda t: FROZEN_CELLS)

    assert steps == [Step.COUNTED, Step.COUNTED, Step.COUNTED, Step.COUNTED, Step.STOPPED]


def test_an_unreadable_reading_clears_the_run():
    tracker = HeartbeatTracker()
    tracker.observe(STREAM, 1, 0.0, FROZEN_CELLS)
    tracker.observe(STREAM, 2, 1.0, FROZEN_CELLS)

    assert tracker.observe(STREAM, 3, 1.5, None) is Step.INVALID
    assert tracker.observe(STREAM, 4, 2.0, FROZEN_CELLS) is Step.COUNTED
    assert tracker.observe(STREAM, 5, 3.0, FROZEN_CELLS) is Step.COUNTED
    assert tracker.observe(STREAM, 6, 4.0, FROZEN_CELLS) is Step.STOPPED


def test_a_new_stream_clears_the_run_and_its_first_reading_starts_again():
    tracker = HeartbeatTracker()
    tracker.observe(STREAM, 1, 0.0, FROZEN_CELLS)
    tracker.observe(STREAM, 2, 1.0, FROZEN_CELLS)

    # The new stream restarts sequence numbers: a lower sequence must still count as a new frame.
    assert tracker.observe("stream-b", 1, 2.0, FROZEN_CELLS) is Step.COUNTED
    assert tracker.observe("stream-b", 2, 3.0, FROZEN_CELLS) is Step.COUNTED
    assert tracker.observe("stream-b", 3, 4.0, FROZEN_CELLS) is Step.STOPPED


def test_a_stopped_display_recovers_at_once_when_its_cells_change():
    tracker = HeartbeatTracker()
    feed(tracker, [0.0, 1.0, 2.0], lambda t: FROZEN_CELLS)

    assert tracker.observe(STREAM, 4, 3.0, healthy_cells(3.0)) is Step.CHANGED
    assert tracker.observe(STREAM, 5, 4.0, healthy_cells(4.0)) is Step.CHANGED
    assert tracker.observe(STREAM, 6, 5.0, healthy_cells(5.0)) is Step.CHANGED


def test_cells_of_is_none_unless_all_four_cells_are_readable():
    assert cells_of(FrameObservation(True, heartbeat=(True, False, None, True))) is None
    assert cells_of(FrameObservation(False, reason="stale_frame")) is None
    assert cells_of(FrameObservation(True, heartbeat=(True, False, False, True))) == (True, False, False, True)


def test_a_reliable_reading_with_an_unreadable_heartbeat_is_not_visible_and_keeps_its_state():
    obs = FrameObservation(True, heartbeat=(None, False, False, False), capacity=4, statuses=("PASS",) * 4)

    unreadable = unreadable_heartbeat(obs)

    assert unreadable.reliable is False
    assert unreadable.reason == HEARTBEAT_UNREADABLE


def test_a_reading_that_is_already_unreliable_keeps_its_own_reason():
    obs = FrameObservation(False, reason="locator_orientation_or_contrast_invalid")

    assert unreadable_heartbeat(obs) is obs


def test_changed_within_is_true_for_three_seconds_after_a_counted_change_and_reset_forgets_it():
    tracker = HeartbeatTracker()
    feed(tracker, [0.0, 1.0], healthy_cells)  # CHANGED at 1.0 (cells differ by then)

    assert tracker.changed_within(1.0 + 3.0) is True
    assert tracker.changed_within(1.0 + 3.1) is False
    tracker.reset()
    assert tracker.changed_within(1.5) is False


# --- the HID probe -------------------------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeReader:
    """The probe's two callables. ``cells_at(t)`` gives the heartbeat cells at time t; they ride in the snapshot."""

    def __init__(self, clock, cells_at, sequence_step=True):
        self.clock = clock
        self.cells_at = cells_at
        self.sequence = 0
        self.sequence_step = sequence_step

    def latest(self):
        now = self.clock()
        if self.sequence_step:
            self.sequence += 1
        return FrameSnapshot(self.cells_at(now), self.sequence, now, STREAM, None)

    @staticmethod
    def decode(snapshot):
        return FrameObservation(True, heartbeat=snapshot.image, capacity=4)


def test_probe_reports_stopped_within_the_budget_when_the_display_holds_still():
    clock = FakeClock()
    tracker = HeartbeatTracker()
    reader = FakeReader(clock, lambda t: FROZEN_CELLS)

    stopped = probe(tracker, reader.latest, reader.decode, clock=clock, sleep=clock.sleep)

    assert stopped is True
    assert clock.now - 100.0 <= PROBE_MAX_SEC


def test_probe_lets_a_live_display_through_at_the_first_counted_change():
    clock = FakeClock()
    tracker = HeartbeatTracker()
    reader = FakeReader(clock, healthy_cells)

    stopped = probe(tracker, reader.latest, reader.decode, clock=clock, sleep=clock.sleep)

    assert stopped is False
    assert clock.now - 100.0 < 2.0


def test_probe_without_any_frame_goes_on_as_not_stopped():
    clock = FakeClock()

    stopped = probe(HeartbeatTracker(), lambda: None, None, clock=clock, sleep=clock.sleep)

    assert stopped is False


def test_probe_with_no_new_frame_gives_up_at_the_budget_and_goes_on():
    clock = FakeClock()
    reader = FakeReader(clock, lambda t: FROZEN_CELLS, sequence_step=False)
    reader.sequence = 9

    stopped = probe(HeartbeatTracker(), reader.latest, reader.decode, clock=clock, sleep=clock.sleep)

    assert stopped is False
    assert clock.now - 100.0 >= PROBE_MAX_SEC


# --- the reading gate ----------------------------------------------------------------------------------

def _frame(state="standby", heartbeat=0):
    return render_log_solution(state, ["WAITING"] * 4, heartbeat=heartbeat)


def test_gate_reports_heartbeat_stopped_without_judging_the_markers():
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, heartbeat=HeartbeatTracker())
    decisions = []
    for index, t in enumerate([10.0, 11.0, 12.0]):
        decisions.append(gate.observe(_frame("monitoring"), index + 1, t, now=t, stream_id=STREAM,
                                      pts=100.0 + index))

    assert all(decision.kind != "heartbeat_stopped" for decision in decisions[:2])
    assert decisions[2].kind == "heartbeat_stopped"
    assert decisions[2].reason == HEARTBEAT_STOPPED


def test_gate_with_a_moving_heartbeat_judges_the_markers_as_before():
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, heartbeat=HeartbeatTracker())
    kinds = []
    for index, t in enumerate([10.0, 11.0, 12.0, 13.0]):
        decision = gate.observe(_frame("standby", heartbeat=index), index + 1, t, now=t, stream_id=STREAM,
                                pts=100.0 + index)
        kinds.append(decision.kind)

    assert "heartbeat_stopped" not in kinds


def test_gate_turns_an_unreadable_heartbeat_into_an_invisible_reading():
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, heartbeat=HeartbeatTracker())
    frame = _frame("standby", heartbeat=0)
    # Cover the first heartbeat cell with mid-grey (contract 1.3: x 278+2..288-2, y 2+2..12-2, scale 2, origin 23/31).
    frame[31 + 2 * 2:31 + 12 * 2, 23 + 278 * 2 + 4:23 + 288 * 2 - 4] = (128, 128, 128)

    decision = gate.observe(frame, 1, 10.0, now=10.0, stream_id=STREAM, pts=100.0)

    assert decision.kind == "unknown"
    assert decision.reason == HEARTBEAT_UNREADABLE


def test_gate_without_a_heartbeat_tracker_is_unchanged():
    gate = RoundFrameGate("DFU-01", require_presentation_time=False)

    decision = gate.observe(_frame("standby"), 1, 10.0, now=10.0, stream_id=STREAM, pts=100.0)

    assert decision.kind == "waiting"


def test_a_frozen_picture_restarts_the_heartbeat_run():
    """Before a freeze two identical readings were counted; the first reading after it must not complete three."""
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, heartbeat=HeartbeatTracker())
    for index, t in enumerate([10.0, 11.0]):
        gate.observe(_frame("monitoring"), index + 1, t, now=t, stream_id=STREAM, pts=100.0 + index)

    frozen = gate.stream.observe_no_frame(now=12.0)
    assert gate.decide(frozen, None, None, None).kind == "frozen"

    decision = gate.observe(_frame("monitoring"), 3, 13.0, now=13.0, stream_id=STREAM, pts=102.0)

    assert decision.kind != "heartbeat_stopped"
