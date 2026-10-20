"""設備輪次（DeviceRound）：一台設備本輪走到哪。

一台設備一個實例，以設備 ID 為 key，Core 存活期間常駐。輸入是 log_solution_reader 的觀察結果
（FrameObservation）；輸出是 TakeDecision，加上指令 handler 問的「現在可以做什麼」。
影格序號與 PTS 凍結判斷留在 frame_stream.FrameStream（經 round_frame_consumer.RoundFrameGate 使用）。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from log_solution_reader import (
    ActivityTracker, CONFIRMATION_REQUIRED, MarkerState, ScreenKind, screen_kind,
)

# check_refusal() 的結果；server 轉成 1.0 錯誤碼。
MONITOR_NOT_READY = "monitor_not_ready"
MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"

# judge() 的 reason：可交付畫面出現，但本輪沒有在本次 check 被 armed（沒見過 Monitoring）。
DELIVERABLE_WITHOUT_MONITORING = "deliverable_without_monitoring"


@dataclass(frozen=True)
class TakeDecision:
    kind: str
    device_id: str
    state: Optional[MarkerState] = None
    capacity: int = 0
    results: Tuple[Tuple[int, str], ...] = ()
    reason: str = ""
    detail: str = ""  # human-readable message for frozen (why the picture was judged frozen)


class DeviceRound:
    """One device's round progress: request, arming, delivery record, input release."""

    def __init__(self, device_id, stable_complete_frames=2, max_frame_gap=1.0):
        self.device_id = str(device_id)
        self.stable_complete_frames = max(1, int(stable_complete_frames))
        self.max_frame_gap = float(max_frame_gap)
        # Set by a shortcut or a Preparing seen in monitor_start; spent by taken or manual_verification_required.
        self.start_requested = False
        self.input_released = False
        self.armed = False
        self.taken = False
        self.armed_capacity = None
        self.delivered_signature = None
        self.delivered_results = ()
        self.last_received_at = None
        self._candidate_signature = None
        self._candidate_count = 0
        self.activity = ActivityTracker(self.max_frame_gap)

    # ---- what the command handlers ask -------------------------------------------------

    def note_start(self):
        """Shortcut sent, or monitor_start is looking at Preparing. Keeps an existing request."""
        self.start_requested = True

    def begin_start(self):
        """A new monitor_start drops the previous delivery; Monitoring must be seen again.

        Keeps a request that monitor_not_started did not clear.
        """
        self.clear_delivery()
        self.input_released = False

    def accepts_round(self):
        """True while a start was requested: a Monitoring reading may arm the round."""
        return self.start_requested

    def check_verdict(self, reason):
        """Verdict for a reliable check reading that is not paused, already_taken or unknown.

        None when judgment should continue. Side effect: MANUAL_VERIFICATION_REQUIRED settles the
        request and releases SN input, so call this once per reading.
        """
        if not self.start_requested:
            return MONITOR_NOT_READY
        if reason == DELIVERABLE_WITHOUT_MONITORING:
            self.settle()
            return MANUAL_VERIFICATION_REQUIRED
        return None

    def settle(self):
        """taken or manual_verification_required spends the request and releases the next SN input."""
        self.start_requested = False
        self.input_released = True

    def released_for_input(self):
        """True after settle() and before the next new round: SN input may go in on a finished screen."""
        return self.input_released

    # ---- what the reader's observations do -----------------------------------------------

    def disarm(self):
        """A new KVM stream: the round must be armed again by a fresh Monitoring frame on it.

        The delivery record stays, so a re-ask of the same round still gets already_taken (#56).
        """
        self.armed = False
        self.taken = False
        self.armed_capacity = None
        self.drop_candidate()
        self.activity.reset()

    def clear_delivery(self):
        """Forget the armed round, its delivery and the started-channel evidence."""
        self.disarm()
        self.delivered_signature = None
        self.delivered_results = ()

    def drop_candidate(self):
        """A reading that is not a stable complete frame breaks the run of identical frames."""
        self._candidate_signature = None
        self._candidate_count = 0

    def lose_continuity(self):
        """A stream-time problem: the run of frames and the started-channel pair both restart."""
        self.drop_candidate()
        self.activity.reset()

    def judge(self, observation, sequence, received_at, accept_round=True):
        """Decide what a frame that passed the stream checks means for this device's round."""
        previous_received_at = self.last_received_at
        self.last_received_at = received_at
        self.activity.observe(observation, sequence, received_at)
        kind = screen_kind(observation)
        if kind is ScreenKind.UNKNOWN:
            self.drop_candidate()
            return TakeDecision("unknown", self.device_id, reason=observation.reason)
        if kind is ScreenKind.PAUSED:
            self.drop_candidate()
            return TakeDecision("paused", self.device_id, observation.state,
                                observation.capacity, reason=CONFIRMATION_REQUIRED)
        if observation.state in (MarkerState.PREPARING, MarkerState.MONITORING):
            return self._new_round_seen(observation, accept_round)
        if observation.state == MarkerState.STANDBY:
            # Standby after this round was seen in Monitoring (and not yet taken) means the round is gone.
            round_lost = self.armed
            self.armed = False
            self.armed_capacity = None
            self.drop_candidate()
            if round_lost:
                return TakeDecision("round_lost", self.device_id, observation.state,
                                    observation.capacity, reason="round_lost")
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="standby")
        signature = (observation.state, observation.capacity, observation.statuses)
        if (self.delivered_signature is not None and signature == self.delivered_signature
                and observation.results_complete):
            return TakeDecision("already_taken", self.device_id, observation.state,
                                observation.capacity, self.delivered_results, "round_already_taken")
        if not self.armed or not accept_round:
            # A deliverable screen this call must not take. The server turns it into
            # manual_verification_required when a start was requested but monitoring was not seen.
            if observation.results_complete:
                return TakeDecision("waiting", self.device_id, observation.state,
                                    observation.capacity, reason=DELIVERABLE_WITHOUT_MONITORING)
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="monitoring_not_observed")
        if observation.capacity != self.armed_capacity:
            self.armed = False
            self.drop_candidate()
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="round_capacity_changed")
        if not observation.results_complete:
            self.drop_candidate()
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

    def _new_round_seen(self, observation, accept_round):
        """The one place for "Preparing / Monitoring = a new round".

        The previous delivery goes and no SN input is released. Preparing also disarms the round;
        Monitoring arms it when this call accepts the round.
        """
        if observation.state == MarkerState.PREPARING:
            self.clear_delivery()
            self.input_released = False
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="preparing")
        self.input_released = False
        self.delivered_signature = None
        self.delivered_results = ()
        self.drop_candidate()
        if accept_round:
            self.armed = True
            self.taken = False
            self.armed_capacity = observation.capacity
        else:
            self.armed = False
        return TakeDecision("waiting", self.device_id, observation.state,
                            observation.capacity, reason="monitoring")
