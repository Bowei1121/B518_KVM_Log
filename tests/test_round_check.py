"""RoundCheck: the judgement order of one check_start / check_finish / inspect poll. No socket, no KVM, no sleep.

Readings are built by hand and the clock is passed in, so each rule is checked on its own.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_round import DeviceRound  # noqa: E402
from log_solution_reader import CONFIRMATION_REQUIRED  # noqa: E402
from round_check import CheckMode, ClickDock, Continue, Reading, Reply, RoundCheck  # noqa: E402

PROOF_WAIT = 1.5
POLL = 0.5
LOST = 30.0
MAX_DOCK = 3


def make(mode=CheckMode.CHECK_FINISH, *, round_state=None, channels=4, timeout=600.0, started=0.0):
    return RoundCheck(mode, channels, timeout, started, round_state=round_state,
                      proof_wait_sec=PROOF_WAIT, poll_sec=POLL,
                      visibility_lost_sec=LOST, max_dock_recoveries=MAX_DOCK)


def waiting(reason="monitoring", capacity=4, active=()):
    return Reading(kind="waiting", state="monitoring", reason=reason, capacity=capacity, active=list(active))


def unseen(reason="monitoring_not_observed"):
    return Reading.unknown(reason)


def complete(capacity=4, results=(("PASS", 1),)):
    return Reading(kind="taken", state="complete", reason="reliable_complete", capacity=capacity,
                   results=[{"slot": slot, "status": status} for status, slot in results])


def requested_round():
    rnd = DeviceRound("DFU-01")
    rnd.note_start()
    return rnd


def reply_of(outcome):
    assert isinstance(outcome, Reply), outcome
    return outcome.payload


# ---- order: freshness, capacity, paused / already_taken before the round-start request -------------

def test_freshness_pending_inside_the_proof_window_waits_and_keeps_the_round():
    check = make(round_state=DeviceRound("DFU-01"))

    outcome = check.step(Reading.unknown("freshness_pending"), now=0.2)

    assert outcome == Continue(min(POLL, PROOF_WAIT - 0.2))


def test_freshness_pending_past_the_proof_window_is_judged_like_any_unknown_reading():
    check = make(round_state=DeviceRound("DFU-01"))

    outcome = check.step(Reading.unknown("freshness_pending"), now=PROOF_WAIT + 0.1)

    assert outcome == Continue(POLL)  # unknown, not a visibility loss: poll on


def test_timeout_during_the_proof_window_replies_timeout():
    check = make(round_state=DeviceRound("DFU-01"), timeout=1.0)

    payload = reply_of(check.step(Reading.unknown("freshness_pending"), now=1.0))

    assert payload["status"] == "timeout"
    assert payload["data"]["reason"] == "freshness_pending"


def test_capacity_mismatch_is_an_error_and_keeps_the_request():
    rnd = requested_round()
    check = make(round_state=rnd, channels=4)

    payload = reply_of(check.step(complete(capacity=10), now=1.0))

    assert payload["error"] == "capacity_mismatch"
    assert payload["data"]["kind"] == "unknown"
    assert payload["data"]["results"] == []
    assert rnd.start_requested is True
    assert rnd.released_for_input() is False


def test_paused_replies_before_the_round_start_request_is_checked():
    check = make(round_state=DeviceRound("DFU-01"))
    paused = Reading(kind="paused", state="review", reason=CONFIRMATION_REQUIRED, capacity=4)

    payload = reply_of(check.step(paused, now=1.0))

    assert payload == {"status": "ok", "data": paused.as_data()}


def test_already_taken_replies_before_monitor_not_ready():
    check = make(round_state=DeviceRound("DFU-01"))
    again = Reading(kind="already_taken", state="complete", reason="round_already_taken", capacity=4,
                    results=[{"slot": 1, "status": "PASS"}])

    payload = reply_of(check.step(again, now=1.0))

    assert payload["status"] == "ok"
    assert payload["data"]["kind"] == "already_taken"
    assert payload["data"]["results"] == [{"slot": 1, "status": "PASS"}]


# ---- the round-start request ---------------------------------------------------------------------

def test_no_request_is_monitor_not_ready_with_the_reading_as_data():
    check = make(round_state=DeviceRound("DFU-01"))

    payload = reply_of(check.step(waiting(), now=1.0))

    assert payload["status"] == "error"
    assert payload["error"] == "monitor_not_ready"
    assert payload["data"]["kind"] == "unknown"
    assert payload["data"]["reason"] == "monitoring"


def test_deliverable_without_seen_monitoring_is_manual_verification_and_releases_sn_input():
    rnd = requested_round()
    check = make(round_state=rnd)
    deliverable = Reading(kind="waiting", state="complete", reason="deliverable_without_monitoring", capacity=4)

    payload = reply_of(check.step(deliverable, now=1.0))

    assert payload["error"] == "manual_verification_required"
    assert rnd.start_requested is False
    assert rnd.released_for_input() is True


def test_taken_settles_the_round_once_and_replies_ok():
    rnd = requested_round()
    check = make(round_state=rnd)

    payload = reply_of(check.step(complete(), now=1.0))

    assert payload["status"] == "ok"
    assert payload["data"]["kind"] == "taken"
    assert rnd.start_requested is False
    assert rnd.released_for_input() is True


# ---- started, inspect and the single read -------------------------------------------------------

def test_check_start_reports_started_channels_and_keeps_the_request():
    rnd = requested_round()
    check = make(CheckMode.CHECK_START, round_state=rnd)
    active = [{"slot": 2, "evidence": "testing"}]

    payload = reply_of(check.step(waiting(active=active), now=1.0))

    assert payload["data"]["kind"] == "started"
    assert payload["data"]["reason"] == "activity_confirmed"
    assert payload["data"]["active"] == active
    assert rnd.start_requested is True


def test_check_finish_never_reports_started():
    check = make(CheckMode.CHECK_FINISH, round_state=requested_round(), timeout=0)

    payload = reply_of(check.step(waiting(active=[{"slot": 2, "evidence": "testing"}]), now=1.0))

    assert payload["data"]["kind"] == "waiting"


def test_inspect_never_settles_and_reports_started_without_a_request():
    check = make(CheckMode.INSPECT, timeout=0)
    active = [{"slot": 1, "evidence": "testing"}]

    payload = reply_of(check.step(waiting(active=active), now=1.0))

    assert payload["data"]["kind"] == "started"


def test_inspect_ends_on_a_finished_screen():
    check = make(CheckMode.INSPECT, timeout=600)
    finished = Reading(kind="waiting", state="complete", reason="complete", capacity=4, finished=True)

    assert reply_of(check.step(finished, now=1.0))["status"] == "ok"


def test_timeout_zero_reads_once():
    check = make(CheckMode.CHECK_FINISH, round_state=requested_round(), timeout=0)

    payload = reply_of(check.step(waiting("confirming_complete_frame"), now=1.0))

    assert payload == {"status": "ok", "data": waiting("confirming_complete_frame").as_data()}


def test_timeout_reports_the_last_reading():
    check = make(CheckMode.CHECK_FINISH, round_state=requested_round(), timeout=5.0)

    payload = reply_of(check.step(waiting("confirming_complete_frame"), now=5.0))

    assert payload["status"] == "timeout"
    assert payload["data"]["reason"] == "confirming_complete_frame"


def test_waiting_polls_again_until_the_timeout():
    check = make(CheckMode.CHECK_FINISH, round_state=requested_round(), timeout=5.0)

    assert check.step(waiting("confirming_complete_frame"), now=1.0) == Continue(POLL)


# ---- visibility and Dock recovery ----------------------------------------------------------------

def test_stream_problem_is_not_visibility_loss_and_never_clicks_the_dock():
    check = make(round_state=requested_round(), timeout=600)

    for now in range(0, 120, 10):
        outcome = check.step(unseen("stale_frame"), now=float(now))
        assert isinstance(outcome, Continue)
    assert check.dock_recoveries == 0


def test_unseen_longer_than_the_lost_time_clicks_the_dock_once():
    check = make(round_state=requested_round(), timeout=600)

    assert check.step(unseen(), now=0.0) == Continue(POLL)
    assert check.step(unseen(), now=LOST - 1) == Continue(POLL)
    assert check.step(unseen(), now=LOST) == ClickDock()
    assert check.dock_recoveries == 1


def test_seen_reading_restarts_the_visibility_timer():
    check = make(round_state=requested_round(), timeout=600)
    check.step(unseen(), now=0.0)
    check.step(waiting(), now=LOST - 1)  # seen: the timer restarts

    assert check.step(unseen(), now=LOST + 1) == Continue(POLL)
    assert check.step(unseen(), now=LOST + 1 + LOST) == ClickDock()


def test_after_the_last_dock_click_unseen_replies_not_visible():
    check = make(round_state=requested_round(), timeout=10000)
    now = 0.0
    for _ in range(MAX_DOCK):
        check.step(unseen(), now=now)
        now += LOST
        assert check.step(unseen(), now=now) == ClickDock()

    payload = reply_of(check.step(unseen(), now=now + 1))

    assert payload["error"] == "log_solution_not_visible"
    assert payload["data"]["kind"] == "unknown"


def test_inspect_never_clicks_the_dock():
    check = make(CheckMode.INSPECT, timeout=600)

    for now in (0.0, LOST, LOST * 2, LOST * 3):
        assert check.step(unseen(), now=now) == Continue(POLL)


@pytest.mark.parametrize("mode", [CheckMode.CHECK_START, CheckMode.CHECK_FINISH])
def test_failed_reading_keeps_the_last_reading_as_error_data(mode):
    check = make(mode, round_state=requested_round(), timeout=600)
    check.step(waiting("confirming_complete_frame"), now=0.0)

    payload = check.fail("frame_frozen", "frozen")

    assert payload == {"status": "error", "error": "frame_frozen", "message": "frozen",
                       "data": {"kind": "unknown", "state": "monitoring", "reason": "confirming_complete_frame",
                                "capacity": 4, "active": [], "results": []}}


def test_a_seen_reading_restarts_the_lost_timer_but_not_the_dock_count():
    """Within one request the click count only grows: seen readings between clicks do not reset it."""
    check = make(round_state=requested_round(), timeout=10000)
    now = 0.0
    for expected in range(1, MAX_DOCK + 1):
        assert check.step(unseen(), now=now) == Continue(POLL)  # starts the lost timer
        now += LOST
        assert check.step(unseen(), now=now) == ClickDock()
        assert check.dock_recoveries == expected
        now += 1
        check.step(waiting(), now=now)  # seen: the timer restarts, the count stays
        now += 1

    payload = reply_of(check.step(unseen(), now=now))

    assert payload["error"] == "log_solution_not_visible"
    assert check.dock_recoveries == MAX_DOCK
