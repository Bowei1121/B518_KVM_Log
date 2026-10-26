"""FrameStream unit tests on a fake clock (ADR-0005, 2026-10-20 revision).

Covers the passive judgement (advance, pending, frozen, missing PTS, stream reset, sequence
regression), the active window for one-shot commands, and the status view of the last verdict.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from fake_kvm import make_snapshot  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from frame_stream import (  # noqa: E402
    FROZEN, LIVE, PENDING, FrameStream,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _stream(threshold: float = 1.0, **kwargs):
    clock = FakeClock()
    return FrameStream("DFU-01", freeze_threshold=threshold, clock=clock, **kwargs), clock


class TestPassiveJudgement:
    def test_first_frame_is_not_proof_of_liveness(self):
        stream, _ = _stream()
        verdict = stream.observe("s", 1, 10.0)
        assert verdict.kind == LIVE and verdict.reason == "first_frame"
        assert stream.last_advanced() is False

    def test_advancing_pts_is_live_and_proven(self):
        stream, clock = _stream()
        stream.observe("s", 1, 10.0)
        clock.advance(0.033)
        verdict = stream.observe("s", 2, 10.033)
        assert verdict.kind == LIVE and verdict.advanced is True
        assert stream.last_advanced() is True
        assert stream.is_frozen is False

    def test_static_pts_within_threshold_is_pending_not_frozen(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(0.5)
        verdict = stream.observe("s", 2, 10.0)
        assert verdict.kind == PENDING
        assert verdict.static_seconds == 0.5
        assert stream.is_frozen is False

    def test_static_pts_reaching_threshold_is_frozen(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(1.0)
        verdict = stream.observe("s", 2, 10.0)
        assert verdict.kind == FROZEN and verdict.reason == "timestamp_static"
        assert verdict.static_seconds == 1.0
        assert stream.is_frozen is True

    def test_frozen_stream_recovers_when_pts_moves_again(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(1.2)
        assert stream.observe("s", 2, 10.0).kind == FROZEN
        clock.advance(0.1)
        assert stream.observe("s", 3, 10.1).kind == LIVE
        assert stream.is_frozen is False

    def test_missing_pts_is_frozen_with_message(self):
        stream, _ = _stream()
        verdict = stream.observe("s", 1, None)
        assert verdict.kind == FROZEN and verdict.reason == "missing_pts"
        assert "Missing Presentation Timestamp" in verdict.message

    def test_missing_pts_is_live_only_when_not_required(self):
        stream, _ = _stream(require_pts=False)
        assert stream.observe("s", 1, None).kind == LIVE

    def test_no_frame_is_frozen(self):
        stream, _ = _stream()
        verdict = stream.observe_no_frame()
        assert verdict.kind == FROZEN and verdict.reason == "no_frame"
        assert stream.is_frozen is True

    def test_new_stream_restarts_the_baseline(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("old", 1, 100.0)
        clock.advance(0.5)
        stream.observe("old", 2, 100.0)
        clock.advance(0.6)
        verdict = stream.observe("new", 1, 1.0)
        assert verdict.stream_changed is True
        assert verdict.kind == LIVE and verdict.reason == "first_frame"
        assert stream.is_frozen is False

    def test_new_stream_clears_a_frozen_verdict(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("old", 1, 100.0)
        clock.advance(1.5)
        assert stream.observe("old", 2, 100.0).kind == FROZEN
        verdict = stream.observe("new", 1, 1.0)
        assert verdict.kind == LIVE and verdict.stream_changed is True
        assert stream.is_frozen is False

    def test_missing_pts_on_new_stream_waits_for_the_next_pts_frame(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("old", 1, 100.0)
        assert stream.observe("new", 1, None).kind == FROZEN
        clock.advance(0.1)
        assert stream.observe("new", 2, 1.0).reason == "first_frame"
        clock.advance(0.1)
        assert stream.observe("new", 3, 1.1).kind == LIVE

    def test_sequence_regression_is_not_fresh_but_static_pts_still_freezes(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 5, 10.0)
        clock.advance(0.1)
        regressed = stream.observe("s", 4, 10.1)
        assert regressed.fresh is False
        assert regressed.kind == LIVE and regressed.advanced is True
        clock.advance(1.2)
        repeated = stream.observe("s", 4, 10.1)
        assert repeated.fresh is False
        assert repeated.kind == FROZEN

    def test_observe_snapshot_reads_the_same_fields(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe_snapshot(make_snapshot(None, sequence=1, stream_id="s", pts=5.0))
        clock.advance(0.2)
        verdict = stream.observe_snapshot(make_snapshot(None, sequence=2, stream_id="s", pts=5.1))
        assert verdict.advanced is True


class TestActiveWindow:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_recent_advance_passes_without_reading(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(0.1)
        stream.observe("s", 2, 10.1)
        clock.advance(0.2)
        reads = []

        def read():
            reads.append(1)
            return None

        verdict = self._run(stream.wait_live(read, sleep=lambda s: asyncio.sleep(0)))
        assert verdict.kind == LIVE
        assert reads == []

    def test_advance_older_than_threshold_is_reread(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(0.1)
        stream.observe("s", 2, 10.1)  # advance at 0.1
        clock.advance(1.2)  # now 1.3: the advance is older than the threshold
        reads = []

        def read():
            reads.append(1)
            return make_snapshot(None, sequence=3, stream_id="s", pts=10.1)

        verdict = self._run(stream.wait_live(read, sleep=lambda s: asyncio.sleep(0)))
        assert reads == [1]
        assert verdict.kind == FROZEN

    def test_static_reading_after_an_advance_is_reread(self):
        stream, clock = _stream(threshold=1.0)
        stream.observe("s", 1, 10.0)
        clock.advance(0.1)
        stream.observe("s", 2, 10.1)  # advance at 0.1
        clock.advance(0.1)
        stream.observe("s", 3, 10.1)  # the last reading shows no advance
        reads = []

        def read():
            reads.append(1)
            return make_snapshot(None, sequence=4, stream_id="s", pts=10.2)

        verdict = self._run(stream.wait_live(read, sleep=lambda s: asyncio.sleep(0)))
        assert reads == [1]
        assert verdict.advanced is True

    def test_window_ends_frozen_when_stream_keeps_changing_without_pts_advance(self):
        stream, clock = _stream(threshold=0.5)
        ids = iter(range(1000))

        def read():
            return make_snapshot(None, sequence=1, stream_id=f"s{next(ids)}", pts=10.0)

        async def fake_sleep(seconds):
            clock.advance(seconds)

        verdict = self._run(stream.wait_live(read, poll_interval=0.1, sleep=fake_sleep))
        assert verdict.kind == FROZEN and verdict.reason == "window_expired"

    def test_window_waits_for_an_advance(self):
        stream, clock = _stream(threshold=1.0)
        pts = iter([10.0, 10.0, 10.0, 10.2])
        seq = iter(range(1, 10))

        def read():
            return make_snapshot(None, sequence=next(seq), stream_id="s", pts=next(pts))

        async def fake_sleep(seconds):
            clock.advance(seconds)

        verdict = self._run(stream.wait_live(lambda: read(), poll_interval=0.1, sleep=fake_sleep))
        assert verdict.kind == LIVE and verdict.advanced is True

    def test_window_without_advance_ends_frozen_at_threshold(self):
        stream, clock = _stream(threshold=0.5)
        seq = iter(range(1, 100))

        def read():
            return make_snapshot(None, sequence=next(seq), stream_id="s", pts=10.0)

        async def fake_sleep(seconds):
            clock.advance(seconds)

        verdict = self._run(stream.wait_live(read, poll_interval=0.1, sleep=fake_sleep))
        assert verdict.kind == FROZEN
        assert stream.is_frozen is True


class TestStatusView:
    def test_frozen_device_stays_listed_until_pts_advances(self):
        clock = FakeClock()
        guard = FrameFreezeGuard(freeze_threshold=1.0, clock=clock)
        stream = guard.stream("BT-01")
        stream.observe("s", 1, 10.0)
        clock.advance(1.5)
        stream.observe("s", 2, 10.0)

        assert guard.get_status()["frozen_devices"] == ["BT-01"]

        clock.advance(3600)
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

        stream.observe("s", 3, 10.1)
        status = guard.get_status()
        assert status["frozen_devices"] == []
        assert status["frozen_devices_count"] == 0
        assert status["tracked_devices_count"] == 1

    def test_frozen_device_stays_listed_until_new_stream_pts_advances(self):
        clock = FakeClock()
        guard = FrameFreezeGuard(freeze_threshold=1.0, clock=clock)
        stream = guard.stream("BT-01")
        stream.observe("old", 1, 100.0)
        clock.advance(1.5)
        stream.observe("old", 2, 100.0)
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

        stream.observe("new", 1, 1.0)
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

        clock.advance(0.1)
        assert stream.observe("new", 2, 1.0).kind == PENDING
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

        clock.advance(0.1)
        stream.observe("new", 3, 1.1)
        assert guard.get_status()["frozen_devices"] == []

    def test_one_stream_per_device(self):
        guard = FrameFreezeGuard(freeze_threshold=1.0, clock=FakeClock())
        assert guard.stream("DFU-01") is guard.stream("DFU-01")
        assert guard.stream("DFU-01") is not guard.stream("DFU-02")
        assert guard.stream("DFU-01").freeze_threshold == 1.0

    def test_status_does_not_take_a_sample(self):
        clock = FakeClock()
        guard = FrameFreezeGuard(freeze_threshold=1.0, clock=clock)
        stream = guard.stream("FCT-01")
        stream.observe("s", 1, 10.0)
        before = stream.last_verdict()
        guard.get_status()
        assert stream.last_verdict() is before

    def test_kvm_lost_releases_new_latch_and_tracking_continues(self):
        clock = FakeClock()
        guard = FrameFreezeGuard(freeze_threshold=1.0, clock=clock)
        stream = guard.stream("BT-01")
        stream.observe("s", 1, 10.0)
        clock.advance(1.0)
        verdict = stream.observe("s", 2, 10.0)
        assert guard.get_status()["frozen_devices"] == ["BT-01"]
        stream.release_kvm_lost_latch(verdict)
        assert guard.get_status()["frozen_devices"] == []
        assert stream.last_verdict() is verdict
        assert stream.is_frozen is True
        assert stream.observe("s", 3, 10.1).advanced is True
        assert guard.get_status()["frozen_devices"] == []
        clock.advance(1.0)
        assert stream.observe("s", 4, 10.1).kind == FROZEN
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

    def test_kvm_loss_keeps_latch_from_an_earlier_verdict(self):
        guard = FrameFreezeGuard(clock=FakeClock())
        stream = guard.stream("BT-01")
        first = stream.observe("s", 1, None)
        second = stream.observe("s", 2, None)
        assert first.kind == second.kind == FROZEN
        stream.release_kvm_lost_latch(second)
        assert guard.get_status()["frozen_devices"] == ["BT-01"]

    def test_kvm_loss_releases_own_latch_after_another_lane_records(self):
        guard = FrameFreezeGuard(clock=FakeClock())
        stream = guard.stream("BT-01")
        first = stream.observe("s", 1, None)
        second = stream.observe("s", 2, None)
        assert first is not second
        stream.release_kvm_lost_latch(first)
        assert guard.get_status()["frozen_devices"] == []
