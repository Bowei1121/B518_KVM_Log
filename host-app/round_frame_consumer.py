"""Frame consumer for one device: stream order, PTS freeze, then the device's round.

Reading the screen is log_solution_reader's job. The device's round progress lives in
device_round.DeviceRound; this module owns what is about the frame stream (sequence, stream
identity, presentation-time freeze).
"""

import time

from device_round import DeviceRound, TakeDecision
from log_solution_reader import inspect_app_frame


class RoundFrameGate:
    """Frame-stream checks for one device, then its DeviceRound decides what the frame means."""

    def __init__(self, device_id, stable_complete_frames=2, max_age=1.0,
                 max_frame_gap=1.0, clock=time.monotonic,
                 require_presentation_time=False,
                 freeze_threshold=1.0, round_state=None):
        self.device_id = str(device_id)
        self.max_age = float(max_age)
        self.clock = clock
        self.require_presentation_time = bool(require_presentation_time)
        self.freeze_threshold = float(freeze_threshold)
        # The server shares one DeviceRound per device. When round_state is given, its
        # stable_complete_frames and max_frame_gap win and this gate ignores those two arguments.
        self.round = round_state if round_state is not None else DeviceRound(
            self.device_id, stable_complete_frames, max_frame_gap)
        self.is_frozen = False
        self.last_sequence = None
        self.last_stream_id = None
        self.last_presentation_time = None
        self.first_static_presentation_time = None

    @property
    def last_active(self):
        """Channels the last reading confirmed as started (check's ``active``)."""
        return self.round.activity.last_active

    def _reset_freeze_baseline(self):
        """Stream change: the sequence and presentation-time baseline starts over."""
        self.last_sequence = None
        self.is_frozen = False
        self.last_presentation_time = None
        self.first_static_presentation_time = None

    def observe(self, frame, sequence, received_at, now=None, stream_id=None,
                pts=None, accept_round=True):
        """Consume a fresh raw frame and return waiting/paused/taken/already_taken/unknown/frozen."""
        now = self.clock() if now is None else now
        if stream_id != self.last_stream_id:
            self.last_stream_id = stream_id
            self._reset_freeze_baseline()
            # A reconnect may show a retained Complete screen from a previous
            # process/session. Require a fresh Monitoring frame on this stream before
            # arming again. The delivery record stays, so a re-ask of the same round
            # still gets already_taken (#56).
            self.round.disarm()
        if self.last_sequence is not None and sequence <= self.last_sequence:
            self.round.activity.clear_active()
            return TakeDecision("waiting", self.device_id, reason="duplicate_or_out_of_order_frame")
        self.last_sequence = sequence
        if self.require_presentation_time and pts is None:
            self.round.lose_continuity()
            return TakeDecision("unknown", self.device_id, reason="missing_source_frame_time")
        if pts is not None:
            if self.last_presentation_time is not None:
                if pts <= self.last_presentation_time:
                    if self.first_static_presentation_time is None:
                        self.first_static_presentation_time = now
                    static_duration = max(0.0, now - self.first_static_presentation_time)
                    if static_duration >= self.freeze_threshold:
                        self.is_frozen = True
                        self.round.lose_continuity()
                        return TakeDecision("frozen", self.device_id, reason="frame_frozen")
                    self.round.lose_continuity()
                    return TakeDecision("unknown", self.device_id,
                                        reason="out_of_order_source_frame")
                else:
                    self.first_static_presentation_time = None
                    self.is_frozen = False
            else:
                self.first_static_presentation_time = now
            self.last_presentation_time = pts
        observation = inspect_app_frame(frame, received_at, now, self.max_age)
        return self.round.judge(observation, sequence, received_at, accept_round)


def observe_latest_round_frame(kvm, gate, now=None, accept_round=True):
    """Pass the newest decoded BGR frame through the public consumer seam."""
    snapshot = kvm.latest_frame()
    if snapshot is None:
        return TakeDecision("unknown", gate.device_id, reason="no_frame")
    return gate.observe(snapshot.image, snapshot.sequence, snapshot.received_monotonic, now=now,
                        stream_id=snapshot.stream_id, pts=snapshot.pts, accept_round=accept_round)
