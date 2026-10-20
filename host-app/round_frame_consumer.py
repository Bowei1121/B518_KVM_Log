"""Frame consumer for one device: freshness from its FrameStream, then its DeviceRound decides.

Reading the screen is log_solution_reader's job. The device's round progress lives in
device_round.DeviceRound. Stream identity, sequence order and PTS stillness live in frame_stream.FrameStream.
"""

import time

from device_round import DeviceRound, TakeDecision
from frame_stream import FRESHNESS_PENDING, FROZEN, PENDING, FrameStream, Verdict
from log_solution_reader import inspect_app_frame


class RoundFrameGate:
    """Frame-stream checks for one device, then its DeviceRound decides what the frame means."""

    def __init__(self, device_id, stable_complete_frames=2, max_age=1.0,
                 max_frame_gap=1.0, clock=time.monotonic,
                 require_presentation_time=False,
                 freeze_threshold=1.0, round_state=None, frame_stream=None, require_proof=False):
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

    @property
    def is_frozen(self):
        return self.stream.is_frozen

    @property
    def last_active(self):
        """Channels the last reading confirmed as started (check's ``active``)."""
        return self.round.activity.last_active

    def observe(self, frame, sequence, received_at, now=None, stream_id=None,
                pts=None, accept_round=True):
        """Consume a fresh raw frame and return waiting/paused/taken/already_taken/unknown/frozen.

        With require_proof, a new frame that has not shown PTS moving (a first frame, or a static one)
        is not judged: unknown with reason freshness_pending, round continuity lost.
        """
        now = self.clock() if now is None else now
        verdict = self.stream.observe(stream_id, sequence, pts, now)
        if verdict.stream_changed:
            self._stream_changed()
        if self.require_proof and verdict.kind != FROZEN and verdict.fresh and not verdict.advanced:
            self.round.lose_continuity()
            return TakeDecision("unknown", self.device_id, reason=FRESHNESS_PENDING)
        return self._decide(verdict, frame, sequence, received_at, now, accept_round)

    def observe_missing(self, now=None):
        """No picture from the KVM yet: frozen, the same as a frame without PTS."""
        now = self.clock() if now is None else now
        return self._decide(self.stream.observe_no_frame(now), None, None, None, now, True)

    def _stream_changed(self):
        # A reconnect may show a retained Complete screen from a previous
        # process/session. Require a fresh Monitoring frame on this stream before
        # arming again. The delivery record stays, so a re-ask of the same round
        # still gets already_taken (#56).
        self.round.disarm()

    def _decide(self, verdict: Verdict, frame, sequence, received_at, now, accept_round):
        if verdict.kind == FROZEN:
            self.round.lose_continuity()
            return TakeDecision("frozen", self.device_id, reason="frame_frozen", detail=verdict.message)
        if not verdict.fresh:
            self.round.activity.clear_active()
            return TakeDecision("waiting", self.device_id, reason="duplicate_or_out_of_order_frame")
        if verdict.kind == PENDING:
            self.round.lose_continuity()
            return TakeDecision("unknown", self.device_id, reason="out_of_order_source_frame")
        observation = inspect_app_frame(frame, received_at, now, self.max_age)
        return self.round.judge(observation, sequence, received_at, accept_round)


def observe_latest_round_frame(kvm, gate, now=None, accept_round=True):
    """Pass the newest decoded BGR frame through the public consumer seam."""
    snapshot = kvm.latest_frame()
    if snapshot is None:
        return gate.observe_missing(now)
    return gate.observe(snapshot.image, snapshot.sequence, snapshot.received_monotonic, now=now,
                        stream_id=snapshot.stream_id, pts=snapshot.pts, accept_round=accept_round)
