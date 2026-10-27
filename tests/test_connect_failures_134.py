"""Connection failure tracker (issue #134): 3 failures within 60 s, pure logic with an injected clock."""
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from connect_failures import ConnectFailures, ConnectFailureState  # noqa: E402

IP = "192.168.1.10"


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def make(clock=None, **kw):
    clock = clock or Clock()
    return ConnectFailures(clock=clock, **kw), clock


def test_defaults_are_3_60_10():
    tracker, _ = make()
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (3, 60.0, 10.0)


def test_unknown_ip_has_the_quiet_default_state():
    tracker, clock = make()
    state = tracker.state(IP)
    assert (state.fail_count, state.connect_failed, state.retrying) == (0, False, False)
    assert state.first_fail_at is None and state.last_error is None and state.next_retry_at is None
    assert state.retry_in_sec(clock()) is None
    assert tracker.due(clock()) == []


def test_state_is_frozen():
    tracker, _ = make()
    with pytest.raises(FrozenInstanceError):
        tracker.state(IP).fail_count = 5  # type: ignore[misc]


def test_one_and_two_failures_do_not_alarm():
    tracker, clock = make()
    tracker.record_failure(IP)
    assert (tracker.state(IP).fail_count, tracker.state(IP).connect_failed) == (1, False)
    clock.advance(10)
    tracker.record_failure(IP)
    assert (tracker.state(IP).fail_count, tracker.state(IP).connect_failed) == (2, False)


def test_third_failure_within_window_raises_alarm():
    tracker, clock = make()
    for _ in range(3):
        tracker.record_failure(IP)
        clock.advance(10)
    state = tracker.state(IP)
    assert state.fail_count == 3 and state.connect_failed is True
    assert state.first_fail_at == 1000.0
    assert state.last_error == "kvm_connect_failed"


def test_boundary_exactly_window_still_alarms():
    tracker, clock = make()
    tracker.record_failure(IP)
    clock.advance(30)
    tracker.record_failure(IP)
    clock.advance(30)
    tracker.record_failure(IP)
    assert tracker.state(IP).connect_failed is True


def test_spread_over_window_does_not_alarm_then_sliding_window_does():
    tracker, clock = make()
    for _ in range(3):  # t=0, 40, 80: first..last = 80 > 60
        tracker.record_failure(IP)
        clock.advance(40)
    assert tracker.state(IP).fail_count == 3
    assert tracker.state(IP).connect_failed is False
    tracker.record_failure(IP)  # t=120: last three 40, 80, 120 span 80 -> still no
    assert tracker.state(IP).connect_failed is False
    clock.advance(-20)  # make the next failure closer: last three 80, 120, 140 span 60
    clock.advance(40)
    tracker.record_failure(IP)
    state = tracker.state(IP)
    assert state.fail_count == 5
    assert state.connect_failed is True
    assert state.first_fail_at == 1000.0  # first of the whole consecutive series


def test_sliding_window_uses_only_the_last_three():
    tracker, clock = make()
    tracker.record_failure(IP)  # 0
    clock.advance(100)
    tracker.record_failure(IP)  # 100
    clock.advance(5)
    tracker.record_failure(IP)  # 105
    assert tracker.state(IP).connect_failed is False
    clock.advance(5)
    tracker.record_failure(IP)  # 110: last three 100,105,110
    assert tracker.state(IP).connect_failed is True


def test_alarm_stays_after_more_failures_even_when_window_is_exceeded():
    tracker, clock = make()
    for _ in range(3):
        tracker.record_failure(IP)
        clock.advance(10)
    assert tracker.state(IP).connect_failed is True
    clock.advance(500)
    tracker.record_failure(IP)  # last three now spread far apart
    assert tracker.state(IP).connect_failed is True
    assert tracker.state(IP).fail_count == 4


def test_success_resets_everything():
    tracker, clock = make()
    for _ in range(3):
        tracker.record_failure(IP, "kvm_connect_failed")
        clock.advance(10)
    tracker.set_retrying(IP, True)
    tracker.record_success(IP)
    assert tracker.state(IP) == ConnectFailureState()
    assert tracker.due(clock() + 1000) == []
    # a new series starts from zero
    tracker.record_failure(IP)
    assert tracker.state(IP).fail_count == 1 and tracker.state(IP).connect_failed is False


def test_success_on_unknown_ip_is_harmless():
    tracker, _ = make()
    tracker.record_success(IP)
    assert tracker.state(IP) == ConnectFailureState()


def test_retry_in_sec_counts_down_with_ceil_and_floor_zero():
    tracker, clock = make()
    tracker.record_failure(IP)
    state = tracker.state(IP)
    assert state.next_retry_at == 1010.0
    assert state.retry_in_sec(clock()) == 10
    clock.advance(0.5)
    assert tracker.state(IP).retry_in_sec(clock()) == 10  # ceil(9.5)
    clock.advance(8.6)
    assert tracker.state(IP).retry_in_sec(clock()) == 1  # ceil(0.9)
    clock.advance(5)
    assert tracker.state(IP).retry_in_sec(clock()) == 0


def test_retry_in_sec_is_none_while_retrying():
    tracker, clock = make()
    tracker.record_failure(IP)
    tracker.set_retrying(IP, True)
    state = tracker.state(IP)
    assert state.retrying is True
    assert state.retry_in_sec(clock()) is None
    tracker.set_retrying(IP, False)
    assert tracker.state(IP).retrying is False


def test_due_lists_ips_whose_time_has_passed_and_are_not_retrying():
    tracker, clock = make()
    tracker.record_failure("10.0.0.2")
    tracker.record_failure("10.0.0.1")
    assert tracker.due(clock()) == []
    clock.advance(9.9)
    assert tracker.due(clock()) == []
    clock.advance(0.1)
    assert tracker.due(clock()) == ["10.0.0.1", "10.0.0.2"]
    tracker.set_retrying("10.0.0.1", True)
    assert tracker.due(clock()) == ["10.0.0.2"]
    tracker.record_success("10.0.0.2")
    assert tracker.due(clock()) == []


def test_failed_retry_schedules_the_next_one_and_keeps_the_alarm():
    tracker, clock = make()
    for _ in range(3):
        tracker.record_failure(IP)
        clock.advance(10)
    assert tracker.state(IP).connect_failed is True
    for _ in range(5):
        tracker.set_retrying(IP, True)
        clock.advance(2)
        tracker.record_failure(IP)  # a failed retry
        state = tracker.state(IP)
        assert state.connect_failed is True and state.retrying is False
        assert state.next_retry_at == clock() + 10.0
        clock.advance(10)
        assert tracker.due(clock()) == [IP]


def test_last_error_is_a_code_only():
    tracker, _ = make()
    tracker.record_failure(IP)
    assert tracker.state(IP).last_error == "kvm_connect_failed"
    tracker.record_failure(IP, "connect_timeout")
    assert tracker.state(IP).last_error == "connect_timeout"


def test_ips_are_independent():
    tracker, clock = make()
    for _ in range(3):
        tracker.record_failure("10.0.0.1")
        clock.advance(1)
    tracker.record_failure("10.0.0.2")
    assert tracker.state("10.0.0.1").connect_failed is True
    assert tracker.state("10.0.0.2").connect_failed is False
    assert tracker.state("10.0.0.2").fail_count == 1
    tracker.record_success("10.0.0.1")
    assert tracker.state("10.0.0.2").fail_count == 1


def test_threshold_two_alarms_on_the_second_failure():
    tracker, clock = make(threshold=2)
    tracker.record_failure(IP)
    clock.advance(1)
    tracker.record_failure(IP)
    assert tracker.state(IP).connect_failed is True


def test_window_30_rejects_a_40_second_spread():
    tracker, clock = make(window_sec=30.0)
    for _ in range(3):
        tracker.record_failure(IP)
        clock.advance(20)  # span 40 > 30
    assert tracker.state(IP).connect_failed is False


def test_retry_interval_5_changes_next_retry():
    tracker, _ = make(retry_interval_sec=5.0)
    tracker.record_failure(IP)
    assert tracker.state(IP).next_retry_at == 1005.0


def test_retry_interval_40_makes_three_failures_miss_the_window():
    tracker, clock = make(retry_interval_sec=40.0)
    for _ in range(3):
        tracker.record_failure(IP)
        clock.advance(40)
    assert tracker.state(IP).connect_failed is False


def test_configure_changes_parameters_but_keeps_clock_and_state():
    tracker, clock = make()
    tracker.record_failure(IP)
    tracker.configure(threshold=2, window_sec=30.0, retry_interval_sec=5.0)
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (2, 30.0, 5.0)
    clock.advance(1)
    tracker.record_failure(IP)
    assert tracker.state(IP).connect_failed is True


def test_alarm_and_clear_are_logged_once_bilingual(caplog):
    tracker, clock = make()
    with caplog.at_level("INFO"):
        for _ in range(5):
            tracker.record_failure(IP)
            clock.advance(1)
        tracker.record_success(IP)
        tracker.record_success(IP)
    raised = [r for r in caplog.records if getattr(r, "zh", None) and "異常" in r.zh and "解除" not in r.zh]
    cleared = [r for r in caplog.records if getattr(r, "zh", None) and "解除" in r.zh]
    assert len(raised) == 1
    assert len(cleared) == 1
