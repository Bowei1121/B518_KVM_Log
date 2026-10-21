"""Frame consumer for one device: freshness from its FrameStream, then its DeviceRound decides.

Reading the screen is log_solution_reader's job. The device's round progress lives in
device_round.DeviceRound. Stream identity, sequence order and PTS stillness live in frame_stream.FrameStream.
"""

import time

from device_round import DeviceRound, TakeDecision
from frame_stream import FRESHNESS_PENDING, FROZEN, PENDING, FrameStream, Verdict
from log_solution_heartbeat import (
    HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE, Step, cells_of, unreadable_heartbeat,
)
from log_solution_reader import inspect_app_frame


class RoundFrameGate:
    """Frame-stream checks for one device, then its DeviceRound decides what the frame means."""

    def __init__(self, device_id, stable_complete_frames=2, max_age=1.0,
                 max_frame_gap=1.0, clock=time.monotonic,
                 require_presentation_time=False,
                 freeze_threshold=1.0, round_state=None, frame_stream=None, require_proof=False,
                 heartbeat=None, stats=None):
        self.device_id = str(device_id)
        self.max_age = float(max_age)
        self.clock = clock
        # The server shares one DeviceRound per device. When round_state is given, its
        # stable_complete_frames and max_frame_gap win and this gate ignores those two arguments.
        self.round = round_state if round_state is not None else DeviceRound(
            self.device_id, stable_complete_frames, max_frame_gap)
        # The server shares one FrameStream per device. When frame_stream is given, its threshold
        # and PTS requirement win and freeze_threshold / require_presentation_time are ignored.
        self.stream = frame_stream if frame_stream is not None else FrameStream(
            self.device_id, freeze_threshold=freeze_threshold, clock=clock,
            require_pts=require_presentation_time)
        # True: a reading that has not shown PTS moving is not judged (freshness_pending). Used by the server.
        self.require_proof = bool(require_proof)
        # The device's HeartbeatTracker (issue #77). None skips the heartbeat step.
        self.heartbeat = heartbeat
        # The device's ReadingStats (issue #94). None skips the recording.
        self.stats = stats
        # The stream generation this gate has acted on. Another reader (inspect) may switch the stream first.
        self._seen_generation = 0

    @property
    def is_frozen(self):
        return self.stream.is_frozen

    @property
    def last_active(self):
        """Channels the last reading confirmed as started (check's ``active``)."""
        return self.round.activity.last_active

    def observe(self, frame, sequence, received_at, now=None, stream_id=None, pts=None):
        """Stream verdict, then the round decision, for one frame passed in directly (tools and tests)."""
        now = self.clock() if now is None else now
        return self.decide(self.stream.observe(stream_id, sequence, pts, now), frame, sequence, received_at,
                           stream_id=stream_id)

    def decide(self, verdict, frame, sequence, received_at, stream_id=None):
        """Round decision for a frame whose stream verdict is already known.

        Returns waiting/paused/taken/already_taken/unknown/frozen/heartbeat_stopped.

        With require_proof, a new frame that has not shown PTS moving (a first frame, or a static one)
        is not judged: unknown with reason freshness_pending, round continuity lost.
        """
        self._sync_generation(verdict)
        if self.require_proof and verdict.kind != FROZEN and verdict.fresh and not verdict.advanced:
            self.round.lose_continuity()
            return TakeDecision("unknown", self.device_id, reason=FRESHNESS_PENDING)
        return self._decide(verdict, frame, sequence, received_at, stream_id)

    def _sync_generation(self, verdict: Verdict):
        if verdict.generation != self._seen_generation:
            self._seen_generation = verdict.generation
            self._stream_changed()

    def _stream_changed(self):
        # A reconnect may show a retained Complete screen from a previous
        # process/session. Require a fresh Monitoring frame on this stream before
        # arming again. The delivery record stays, so a re-ask of the same round
        # still gets already_taken (#56).
        self.round.disarm()

    def _decide(self, verdict: Verdict, frame, sequence, received_at, stream_id=None):
        if verdict.kind == FROZEN:
            self.round.lose_continuity()
            if self.heartbeat is not None:
                self.heartbeat.reset()
            return TakeDecision("frozen", self.device_id, reason="frame_frozen", detail=verdict.message)
        if not verdict.fresh:
            self.round.activity.clear_active()
            return TakeDecision("waiting", self.device_id, reason="duplicate_or_out_of_order_frame")
        if verdict.kind == PENDING:
            self.round.lose_continuity()
            return TakeDecision("unknown", self.device_id, reason="out_of_order_source_frame")
        observation = inspect_app_frame(frame, received_at, verdict.at, self.max_age)
        stopped = False
        if self.heartbeat is not None:
            stopped = self.heartbeat.observe(stream_id, sequence, received_at, cells_of(observation)) is Step.STOPPED
            observation = unreadable_heartbeat(observation)
        # Recorded once per inspected frame, after the heartbeat rule, so "readable" is the final verdict.
        if self.stats is not None:
            self.stats.record(observation, frame)
        if stopped:
            self.round.lose_continuity()
            return TakeDecision("heartbeat_stopped", self.device_id, reason=HEARTBEAT_STOPPED,
                                detail=HEARTBEAT_STOPPED_MESSAGE)
        return self.round.judge(observation, sequence, received_at)


def observe_latest_round_frame(kvm, gate, now=None):
    """Read the newest decoded BGR frame: FrameStream judges its freshness first, then the device's round.

    Blocking (kvm.latest_frame copies the picture and the judgement decodes it): call it from a worker thread.
    """
    snapshot = kvm.latest_frame()
    now = gate.clock() if now is None else now
    verdict = gate.stream.observe_snapshot(snapshot, now)
    if snapshot is None:
        return gate.decide(verdict, None, None, None)
    return gate.decide(verdict, snapshot.image, snapshot.sequence, snapshot.received_monotonic,
                       stream_id=snapshot.stream_id)
