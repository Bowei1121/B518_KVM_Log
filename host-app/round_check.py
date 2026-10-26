"""RoundCheck: the order in which one check_start, check_finish or inspect poll is judged (issue #75, C2).

Each poll gives one Reading: the frame already judged by FrameStream and DeviceRound. step() says what
the caller does next: send a reply, click the Dock, or wait and poll again. Pure and synchronous: no KVM,
no sleep, no asyncio. The caller passes the clock in.

Steps 3 to 7 are docs/PROTOCOL.md 5.4 (判讀順序, 2026-10-19). Steps 1, 2, 8 to 10 are the freshness,
capacity, timeout and visibility rules from the same document (可見性與遺失). The first rule that applies wins:
1. Freshness not yet proven (PTS has not moved) inside the proof window: wait.
2. taken with a capacity other than the device's channels: capacity_mismatch, no delivery.
3. paused and already_taken: reply, before the round-start request is checked.
4. Any other judged reading: round-start request (monitor_not_ready, manual_verification_required).
5. taken: settle the round, reply.
6. check_start and inspect: waiting with active channels -> started.
7. inspect on a finished screen: reply.
8. timeout_sec 0: reply once.
9. timeout_sec reached: timeout with the last reading.
10. check only: visibility. Lost for visibility_lost_sec -> Dock click, at most max_dock_recoveries.
11. Otherwise wait and poll again.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from device_round import MANUAL_VERIFICATION_REQUIRED, MONITOR_NOT_READY
from frame_stream import FRESHNESS_PENDING, Verdict
from log_solution_heartbeat import HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE
from log_solution_reader import FINISHED_STATES, reportable_active


class CheckMode(Enum):
    CHECK_START = "check_start"
    CHECK_FINISH = "check_finish"
    INSPECT = "inspect"


# Picture/stream problems: a Dock click cannot fix them, so they neither count as "Log Solution not
# visible" nor reset the visibility timer. Every other unknown reading counts as not visible
# (wholly or partly covered, closed, minimised), including reasons added later.
STREAM_PROBLEM_REASONS = frozenset({
    "invalid_frame", "stale_frame", "out_of_order_source_frame", "freshness_pending",
    "duplicate_or_out_of_order_frame", "inspection_busy", "inspection_timeout",
})


def active_payload(state, rows) -> List[Dict[str, Any]]:
    """Started channels as reply rows: ``{"slot", "evidence"}``, only for screens that report activity."""
    return [{"slot": slot, "evidence": evidence} for slot, evidence in reportable_active(state, rows)]


@dataclass(frozen=True)
class Reading:
    """One poll's judged reading in the check 1.0 data shape. ``error`` set: the frame stream or the reading failed."""

    kind: str = "unknown"
    state: str = ""
    reason: str = ""
    capacity: int = 0
    active: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    finished: bool = False  # the Log Solution is on a finished screen (inspect ends on it)
    error: Optional[Tuple[str, str]] = None  # (code, message): frame_frozen, round_lost, internal_error

    verdict: Optional[Verdict] = None  # originating FROZEN read; never in wire replies

    @classmethod
    def unknown(cls, reason: str) -> "Reading":
        return cls(reason=reason)

    @classmethod
    def failed(cls, code: str, message: str, verdict: Optional[Verdict] = None) -> "Reading":
        return cls(error=(code, message), verdict=verdict)

    @classmethod
    def from_decision(cls, decision: Any, activity_rows: Optional[Any] = None) -> "Reading":
        """A DeviceRound decision. ``activity_rows`` (check_start only) gives the started channels
        of a waiting screen."""
        if decision.kind == "frozen" or decision.reason == "frame_frozen":
            return cls.failed("frame_frozen", decision.detail or "Log Solution frame stream is frozen",
                              verdict=decision.verdict)
        if decision.kind == "heartbeat_stopped":
            return cls.failed(HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE)
        if decision.kind == "round_lost":
            return cls.failed("round_lost", "Log Solution returned to standby before this round was taken")
        state = decision.state
        active = []
        if activity_rows is not None and decision.kind == "waiting":
            active = active_payload(state, activity_rows)
        return cls(
            kind=decision.kind,
            state=state.value if state is not None else "",
            reason=decision.reason,
            capacity=int(decision.capacity),
            active=active,
            results=[{"slot": slot, "status": status} for slot, status in decision.results],
            finished=state in FINISHED_STATES,
        )

    @classmethod
    def from_inspect(cls, reply: Dict[str, Any], state: Any) -> "Reading":
        """An inspect reply from tcp_server._inspect_round, with the Log Solution state it read."""
        if "error" in reply:
            return cls.failed(reply["error"], reply.get("message", ""), verdict=reply.get("verdict"))
        return cls(kind=reply["kind"], state=reply["state"], reason=reply["reason"], capacity=reply["capacity"],
                   active=reply["active"], results=reply["results"], finished=state in FINISHED_STATES)

    def as_data(self) -> Dict[str, Any]:
        return {"kind": self.kind, "state": self.state, "reason": self.reason, "capacity": self.capacity,
                "active": list(self.active), "results": list(self.results)}


@dataclass(frozen=True)
class Continue:
    """Poll again after ``sleep_sec``."""

    sleep_sec: float


@dataclass(frozen=True)
class ClickDock:
    """Visibility recovery: the caller clicks the Dock icon, waits, then polls again."""


@dataclass(frozen=True)
class Reply:
    """The finished protocol reply (status, error or data)."""

    payload: Dict[str, Any]


Outcome = Union[Continue, ClickDock, Reply]


def visibility_of(reading: Reading) -> str:
    """'ignore' for a stream problem, 'unseen' for any other unknown reading, 'seen' for a reliable one."""
    if reading.reason in STREAM_PROBLEM_REASONS:
        return "ignore"
    return "unseen" if reading.kind == "unknown" else "seen"


class RoundCheck:
    """Judges one request's readings in order. One instance per check_start, check_finish or inspect request."""

    def __init__(self, mode: CheckMode, channels: int, timeout_sec: float, started_at: float, *,
                 round_state: Optional[Any] = None, proof_wait_sec: float, poll_sec: float,
                 visibility_lost_sec: float, max_dock_recoveries: int) -> None:
        self.mode = mode
        self.channels = int(channels)
        self.timeout_sec = float(timeout_sec)
        self.started_at = float(started_at)
        self.round = round_state  # the device's DeviceRound; None for inspect, which never touches the round
        self.proof_wait_sec = float(proof_wait_sec)
        self.poll_sec = float(poll_sec)
        self.visibility_lost_sec = float(visibility_lost_sec)
        self.max_dock_recoveries = int(max_dock_recoveries)
        self.dock_recoveries = 0
        self.last = Reading()  # the last reading that reached step(); error replies keep its data
        self._unseen_since: Optional[float] = None

    def fail(self, code: str, message: str) -> Dict[str, Any]:
        """Error reply carrying the last reading as data, with kind unknown and no results."""
        return {"status": "error", "error": code, "message": message,
                "data": {**self.last.as_data(), "kind": "unknown", "results": []}}

    def step(self, reading: Reading, now: float) -> Outcome:
        elapsed = now - self.started_at
        self.last = reading

        if reading.reason == FRESHNESS_PENDING and elapsed < self.proof_wait_sec:
            if self.timeout_sec > 0 and elapsed >= self.timeout_sec:
                return Reply(self._timeout())
            remaining = self.proof_wait_sec - elapsed
            if self.timeout_sec > 0:
                remaining = min(remaining, self.timeout_sec - elapsed)
            return self._wait(remaining)

        if reading.kind == "taken" and reading.capacity != self.channels:
            return Reply({
                "status": "error",
                "error": "capacity_mismatch",
                "message": f"screen shows {reading.capacity} slots, device is configured for {self.channels}",
                "data": {**reading.as_data(), "kind": "unknown", "reason": "capacity_mismatch", "results": []},
            })
        if reading.kind in ("paused", "already_taken"):
            return Reply({"status": "ok", "data": reading.as_data()})
        if self.round is not None and reading.kind != "unknown":
            refusal = self._round_start_refusal(reading)
            if refusal is not None:
                return Reply(refusal)
        if reading.kind == "taken":
            if self.round is not None:
                self.round.settle()
            return Reply({"status": "ok", "data": reading.as_data()})
        if self.mode is not CheckMode.CHECK_FINISH and reading.kind == "waiting" and reading.active:
            return Reply({"status": "ok", "data": {**reading.as_data(), "kind": "started",
                                                   "reason": "activity_confirmed"}})
        if self.mode is CheckMode.INSPECT and reading.finished:
            return Reply({"status": "ok", "data": reading.as_data()})
        if self.timeout_sec == 0:
            return Reply({"status": "ok", "data": reading.as_data()})
        if elapsed >= self.timeout_sec:
            return Reply(self._timeout())
        if self.mode is not CheckMode.INSPECT:
            dock = self._visibility(reading, now)
            if dock is not None:
                return dock
        return self._wait(self.timeout_sec - elapsed)

    def _round_start_refusal(self, reading: Reading) -> Optional[Dict[str, Any]]:
        """Steps 4 and 5. DeviceRound.check_verdict also settles the request on manual_verification_required."""
        verdict = self.round.check_verdict(reading.reason)
        if verdict == MONITOR_NOT_READY:
            return self.fail("monitor_not_ready", "no round-start request; result was not taken")
        if verdict == MANUAL_VERIFICATION_REQUIRED:
            return self.fail("manual_verification_required",
                             "start was requested but this round's monitoring was not seen")
        return None

    def _visibility(self, reading: Reading, now: float) -> Optional[Outcome]:
        """Step 10. None when the poll should simply go on."""
        visibility = visibility_of(reading)
        if visibility == "unseen":
            if self._unseen_since is None:
                self._unseen_since = now
            if self.dock_recoveries >= self.max_dock_recoveries:
                return Reply(self.fail("log_solution_not_visible",
                                       f"Log Solution not visible after {self.dock_recoveries} Dock clicks"))
            if now - self._unseen_since >= self.visibility_lost_sec:
                self.dock_recoveries += 1
                self._unseen_since = None
                return ClickDock()
        elif visibility == "seen":
            self._unseen_since = None
        return None

    def _timeout(self) -> Dict[str, Any]:
        return {"status": "timeout", "error": "timeout",
                "message": f"no reliable result within {self.timeout_sec:g} s",
                "data": self.last.as_data()}

    def _wait(self, remaining: float) -> Continue:
        return Continue(min(self.poll_sec, max(0.01, remaining)))
