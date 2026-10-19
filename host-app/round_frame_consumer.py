"""Round logic for one device: arm on Monitoring, take each finished round once.

Reading the screen is log_solution_reader's job. This module decides what a reading means for
the device's round (RoundFrameGate) and shapes the legacy reply (tcp_round_reply).
"""

from dataclasses import dataclass
import time
from typing import Optional, Tuple

from log_solution_reader import (
    CONFIRMATION_REQUIRED, ActivityTracker, MarkerState, ScreenKind, inspect_app_frame, screen_kind,
)


@dataclass(frozen=True)
class TakeDecision:
    kind: str
    device_id: str
    state: Optional[MarkerState] = None
    capacity: int = 0
    results: Tuple[Tuple[int, str], ...] = ()
    reason: str = ""


class RoundFrameGate:
    """Require monitoring then a stable complete frame, and take each device round once."""

    def __init__(self, device_id, stable_complete_frames=2, max_age=1.0,
                 max_frame_gap=1.0, clock=time.monotonic,
                 require_presentation_time=False,
                 freeze_threshold=1.0):
        self.device_id = str(device_id)
        self.stable_complete_frames = max(1, int(stable_complete_frames))
        self.max_age = float(max_age)
        self.max_frame_gap = float(max_frame_gap)
        self.clock = clock
        self.require_presentation_time = bool(require_presentation_time)
        self.freeze_threshold = float(freeze_threshold)
        self.armed = False
        self.taken = False
        self.is_frozen = False
        self.last_sequence = None
        self.last_stream_id = None
        self.last_received_at = None
        self.last_presentation_time = None
        self.first_static_presentation_time = None
        self.armed_capacity = None
        self.delivered_signature = None
        self.delivered_results = ()
        self._candidate_signature = None
        self._candidate_count = 0
        self._activity = ActivityTracker(self.max_frame_gap)

    @property
    def last_active(self):
        """Channels the last reading confirmed as started (check's ``active``)."""
        return self._activity.last_active

    @property
    def _activity_prev(self):
        """Compatibility only: tests/test_round_frame_consumer.py reads it. Remove with #55, not a public API."""
        return self._activity.previous

    def forget_delivered_round(self):
        """A new monitor_start drops the previous delivery. Monitoring must be seen again."""
        self.armed = False
        self.taken = False
        self.armed_capacity = None
        self.delivered_signature = None
        self.delivered_results = ()
        self._candidate_signature = None
        self._candidate_count = 0
        self._activity.reset()

    def observe(self, frame, sequence, received_at, now=None, stream_id=None,
                pts=None, accept_round=True):
        """Consume a fresh raw frame and return waiting/paused/taken/already_taken/unknown/frozen."""
        now = self.clock() if now is None else now
        if stream_id != self.last_stream_id:
            self.last_sequence = None
            self.last_stream_id = stream_id
            # A reconnect may show a retained Complete screen from a previous
            # process/session. Require a fresh Monitoring frame on this stream.
            self.armed = False
            self.taken = False
            self.is_frozen = False
            self.last_presentation_time = None
            self.first_static_presentation_time = None
            self.armed_capacity = None
            self.delivered_signature = None
            self.delivered_results = ()
            self._candidate_signature = None
            self._candidate_count = 0
            self._activity.reset()
        if self.last_sequence is not None and sequence <= self.last_sequence:
            self._activity.clear_active()
            return TakeDecision("waiting", self.device_id, reason="duplicate_or_out_of_order_frame")
        self.last_sequence = sequence
        if self.require_presentation_time and pts is None:
            self._candidate_signature = None
            self._candidate_count = 0
            self._activity.reset()
            return TakeDecision("unknown", self.device_id, reason="missing_source_frame_time")
        if pts is not None:
            if self.last_presentation_time is not None:
                if pts <= self.last_presentation_time:
                    if self.first_static_presentation_time is None:
                        self.first_static_presentation_time = now
                    static_duration = max(0.0, now - self.first_static_presentation_time)
                    if static_duration >= self.freeze_threshold:
                        self.is_frozen = True
                        self._candidate_signature = None
                        self._candidate_count = 0
                        self._activity.reset()
                        return TakeDecision("frozen", self.device_id, reason="frame_frozen")
                    self._candidate_signature = None
                    self._candidate_count = 0
                    self._activity.reset()
                    return TakeDecision("unknown", self.device_id,
                                        reason="out_of_order_source_frame")
                else:
                    self.first_static_presentation_time = None
                    self.is_frozen = False
            else:
                self.first_static_presentation_time = now
            self.last_presentation_time = pts
        previous_received_at = self.last_received_at
        self.last_received_at = received_at
        observation = inspect_app_frame(frame, received_at, now, self.max_age)
        self._activity.observe(observation, sequence, received_at)
        kind = screen_kind(observation)
        if kind is ScreenKind.UNKNOWN:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("unknown", self.device_id, reason=observation.reason)

        if kind is ScreenKind.PAUSED:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("paused", self.device_id, observation.state,
                                observation.capacity, reason=CONFIRMATION_REQUIRED)
        if observation.state == MarkerState.PREPARING:
            # Reliable reading, and a new-round signal: drop the previous delivery.
            self.forget_delivered_round()
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="preparing")
        if observation.state == MarkerState.STANDBY:
            # Standby after this round was seen in Monitoring (and not yet taken) means the round is gone.
            round_lost = self.armed
            self.armed = False
            self.armed_capacity = None
            self._candidate_signature = None
            self._candidate_count = 0
            if round_lost:
                return TakeDecision("round_lost", self.device_id, observation.state,
                                    observation.capacity, reason="round_lost")
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="standby")
        if observation.state == MarkerState.MONITORING:
            # Monitoring is a new-round signal: drop the previous delivery. Arm only when
            # this check is allowed to accept the round.
            self.delivered_signature = None
            self.delivered_results = ()
            self._candidate_signature = None
            self._candidate_count = 0
            if accept_round:
                self.armed = True
                self.taken = False
                self.armed_capacity = observation.capacity
            else:
                self.armed = False
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="monitoring")
        signature = (observation.state, observation.capacity, observation.statuses)
        if (self.delivered_signature is not None and signature == self.delivered_signature
                and observation.results_complete):
            return TakeDecision("already_taken", self.device_id, observation.state,
                                observation.capacity, self.delivered_results, "round_already_taken")
        if not self.armed or not accept_round:
            # A deliverable screen that this call must not take. The server turns it into
            # manual_verification_required when a start was requested but monitoring was not seen.
            if observation.results_complete:
                return TakeDecision("waiting", self.device_id, observation.state,
                                    observation.capacity, reason="deliverable_without_monitoring")
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="monitoring_not_observed")
        if observation.capacity != self.armed_capacity:
            self.armed = False
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="round_capacity_changed")
        if not observation.results_complete:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="incomplete_or_nonterminal_results")

        gap = (None if previous_received_at is None else
               max(0.0, received_at - previous_received_at))
        if signature == self._candidate_signature and gap <= self.max_frame_gap:
            self._candidate_count += 1
        else:
            self._candidate_signature = signature
            self._candidate_count = 1
        if self._candidate_count < self.stable_complete_frames:
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="confirming_complete_frame")
        self.taken = True
        self.armed = False
        rows = tuple((index, status) for index, status in enumerate(observation.statuses, 1))
        self.delivered_signature = signature
        self.delivered_results = rows
        return TakeDecision("taken", self.device_id, observation.state,
                            observation.capacity, rows, "reliable_complete")


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
