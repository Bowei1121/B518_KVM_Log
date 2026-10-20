"""Round rules of DeviceRound, driven by reader observations: no socket, no pixels."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_round import (  # noqa: E402
    DELIVERABLE_WITHOUT_MONITORING, MANUAL_VERIFICATION_REQUIRED, MONITOR_NOT_READY, DeviceRound,
)
from log_solution_reader import FrameObservation, MarkerState  # noqa: E402

PASS4 = ("PASS", "PASS", "PASS", "PASS")


def monitoring(capacity=4):
    return FrameObservation(reliable=True, state=MarkerState.MONITORING, capacity=capacity)


def preparing(capacity=4):
    return FrameObservation(reliable=True, state=MarkerState.PREPARING, capacity=capacity)


def standby(capacity=4):
    return FrameObservation(reliable=True, state=MarkerState.STANDBY, capacity=capacity)


def complete(statuses=PASS4):
    return FrameObservation(reliable=True, state=MarkerState.COMPLETE, capacity=len(statuses),
                            statuses=tuple(statuses))


class Feed:
    """Feeds readings with increasing sequence and time, like fresh frames from one stream."""

    def __init__(self, rnd):
        self.rnd = rnd
        self.sequence = 0
        self.received_at = 0.0

    def __call__(self, observation, accept_round=True):
        self.sequence += 1
        self.received_at += 0.1
        return self.rnd.judge(observation, self.sequence, self.received_at, accept_round)


def test_no_start_request_refuses_with_monitor_not_ready():
    rnd = DeviceRound("DFU-01")
    decision = Feed(rnd)(complete())

    assert decision.reason == DELIVERABLE_WITHOUT_MONITORING
    assert rnd.check_verdict(decision.reason) == MONITOR_NOT_READY
    assert rnd.released_for_input() is False


def test_request_without_seen_monitoring_is_manual_verification_and_releases_input():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    decision = Feed(rnd)(complete())

    assert decision.kind == "waiting"
    assert decision.reason == DELIVERABLE_WITHOUT_MONITORING
    assert rnd.check_verdict(decision.reason) == MANUAL_VERIFICATION_REQUIRED
    assert rnd.released_for_input() is True
    assert rnd.accepts_round() is False


def test_monitoring_then_stable_complete_frames_take_the_round_once():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)

    assert feed(monitoring()).reason == "monitoring"
    assert feed(complete()).reason == "confirming_complete_frame"
    taken = feed(complete())

    assert taken.kind == "taken"
    assert taken.results == ((1, "PASS"), (2, "PASS"), (3, "PASS"), (4, "PASS"))


def test_same_signature_after_take_is_already_taken():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)
    feed(monitoring())
    feed(complete())
    taken = feed(complete())

    again = feed(complete())

    assert again.kind == "already_taken"
    assert again.results == taken.results


def test_preparing_clears_the_previous_delivery_and_monitoring_takes_a_new_one():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)
    feed(monitoring())
    feed(complete())
    assert feed(complete()).kind == "taken"

    assert feed(preparing()).reason == "preparing"
    after_preparing = feed(complete())
    assert after_preparing.kind == "waiting"
    assert after_preparing.reason == DELIVERABLE_WITHOUT_MONITORING

    feed(monitoring())
    feed(complete())
    second = feed(complete())
    assert second.kind == "taken"


def test_standby_after_arming_is_round_lost():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)
    feed(monitoring())

    lost = feed(standby())

    assert lost.kind == "round_lost"


def test_sn_input_is_released_by_settle_and_closed_by_the_next_begin_start():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)
    feed(monitoring())
    feed(complete())
    assert feed(complete()).kind == "taken"
    assert rnd.released_for_input() is False

    rnd.settle()
    assert rnd.released_for_input() is True

    rnd.begin_start()
    assert rnd.released_for_input() is False


@pytest.mark.parametrize("new_round", [preparing, monitoring], ids=["preparing", "monitoring"])
def test_preparing_or_monitoring_after_settle_closes_sn_input_again(new_round):
    """A new round seen after settle() closes SN input until its own result is taken."""
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    feed = Feed(rnd)
    feed(monitoring())
    feed(complete())
    assert feed(complete()).kind == "taken"
    rnd.settle()
    assert rnd.released_for_input() is True

    feed(new_round(), accept_round=False)

    assert rnd.released_for_input() is False


def _gate_with_observations(monkeypatch):
    """A gate whose frame "is" the observation already; stream checks and the round are real."""
    import round_frame_consumer
    from round_frame_consumer import RoundFrameGate

    monkeypatch.setattr(round_frame_consumer, "inspect_app_frame",
                        lambda frame, received_at, now, max_age: frame)
    return RoundFrameGate("DFU-01", require_presentation_time=True, freeze_threshold=1.0)


def _look(gate, observation, sequence, received_at, pts, stream_id="s1"):
    return gate.observe(observation, sequence, received_at, now=received_at,
                        stream_id=stream_id, pts=pts)


def _taken_on_stream_s1(gate):
    gate.round.note_start()
    _look(gate, monitoring(), 1, 0.0, pts=1)
    _look(gate, complete(), 2, 0.1, pts=2)
    assert _look(gate, complete(), 3, 0.2, pts=3).kind == "taken"


def test_reconnect_same_round_reask_is_already_taken_with_original_results(monkeypatch):
    gate = _gate_with_observations(monkeypatch)
    _taken_on_stream_s1(gate)

    again = _look(gate, complete(), 1, 1.0, pts=1, stream_id="s2")

    assert again.kind == "already_taken"
    assert again.results == ((1, "PASS"), (2, "PASS"), (3, "PASS"), (4, "PASS"))
    assert gate.taken is False


def test_reconnect_with_changed_screen_is_not_already_taken(monkeypatch):
    gate = _gate_with_observations(monkeypatch)
    _taken_on_stream_s1(gate)

    changed = _look(gate, complete(("PASS", "FAIL", "PASS", "PASS")), 1, 1.0, pts=1, stream_id="s2")

    assert changed.kind != "already_taken"
    assert changed.kind == "waiting"
    assert changed.reason == DELIVERABLE_WITHOUT_MONITORING


def test_reconnect_while_armed_still_requires_manual_verification(monkeypatch):
    """Case A (#56): armed, not yet taken, then the stream drops and a result shows on the new stream."""
    gate = _gate_with_observations(monkeypatch)
    gate.round.note_start()
    _look(gate, monitoring(), 1, 0.0, pts=1)
    assert gate.round.armed is True

    result = _look(gate, complete(), 1, 1.0, pts=1, stream_id="s2")

    assert result.kind == "waiting"
    assert result.reason == DELIVERABLE_WITHOUT_MONITORING
    assert gate.round.check_verdict(result.reason) == MANUAL_VERIFICATION_REQUIRED


def test_new_stream_starts_its_own_freeze_baseline(monkeypatch):
    """A new stream's first frame sets the presentation-time baseline; the old stream's PTS does not carry over."""
    gate = _gate_with_observations(monkeypatch)
    _taken_on_stream_s1(gate)
    _look(gate, complete(), 4, 0.3, pts=3)
    assert _look(gate, complete(), 5, 1.5, pts=3).reason == "frame_frozen"
    assert gate.is_frozen is True

    first = _look(gate, complete(), 1, 1.6, pts=1, stream_id="s2")

    assert first.reason not in ("frame_frozen", "out_of_order_source_frame")
    assert gate.is_frozen is False
