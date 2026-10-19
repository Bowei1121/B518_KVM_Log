"""Frame consumer for one device: stream order, PTS freeze, then the device's round.

Reading the screen is log_solution_reader's job. The device's round progress lives in
device_round.DeviceRound; this module owns what is about the frame stream (sequence, stream
identity, presentation-time freeze) and shapes the legacy reply (tcp_round_reply).
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
        # The server shares one DeviceRound per device; standalone gates own theirs.
        self.round = round_state if round_state is not None else DeviceRound(
            self.device_id, stable_complete_frames, max_frame_gap)
        self.is_frozen = False
        self.last_sequence = None
        self.last_stream_id = None
        self.last_presentation_time = None
        self.first_static_presentation_time = None

    @property
    def taken(self):
        """Compatibility: the round's taken flag, read by tests/test_round_frame_consumer.py."""
        return self.round.taken

    @property
    def last_active(self):
        """Channels the last reading confirmed as started (check's ``active``)."""
        return self.round.activity.last_active

    @property
    def _activity_prev(self):
        """Compatibility only: tests/test_round_frame_consumer.py reads it. Not a public API."""
        return self.round.activity.previous

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
            # process/session. Require a fresh Monitoring frame on this stream.
            self.round.clear_delivery()
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


def tcp_round_reply(decision):
    """Legacy line format, kept only for the offline Ticket 16 evidence tools.

    The TCP server speaks JSON 1.0 (docs/PROTOCOL.md); nothing on the wire uses this.
    """
    if decision.kind == "frozen" or decision.reason == "frame_frozen":
        return "error:frame_frozen\r\n"
    if decision.kind == "taken":
        body = ",".join("{}::{}".format(slot, status)
                        for slot, status in decision.results)
        return "action_done,{}\r\n".format(body)
    if decision.kind == "paused":
        return "action_paused,review\r\n"
    if decision.kind == "already_taken":
        return "action_waiting,already_taken\r\n"
    if decision.kind == "unknown":
        return "action_waiting,unknown,{}\r\n".format(decision.reason)
    return "action_waiting,{}\r\n".format(decision.reason or "monitoring")
