"""FrameStream: one device's picture-freshness state (ADR-0005, 2026-10-20 revision).

Holds the only copy of stream id, sequence order and PTS stillness for a device. Two ways in:

- Passive: every reading the caller already makes (long-poll, inspect, round gate) goes through
  ``observe``. PTS static for ``freeze_threshold`` seconds is frozen.
- Active window: ``wait_live`` for one-shot commands. Reuses the last advance when it is recent.

A missing PTS is frozen (the server always requires PTS). Nothing here touches HID or the round.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from frame_snapshot import FrameSnapshot

FREEZE_THRESHOLD_SEC = 1.0  # PTS static this long is a frozen picture
FRESHNESS_PENDING = "freshness_pending"  # reading not judged: PTS has not been seen to move yet

LIVE = "live"
PENDING = "pending"  # PTS static, but not yet for the threshold: not judged
FROZEN = "frozen"

MISSING_PTS_MESSAGE = "Missing Presentation Timestamp on KVM frame"
NO_FRAME_MESSAGE = "No frame received from KVM yet"


@dataclass(frozen=True)
class Verdict:
    """One judgement of the stream. Immutable, so status can read the last one without the lock."""

    kind: str  # LIVE, PENDING or FROZEN
    reason: str
    at: float  # clock time of the reading
    fresh: bool = True  # sequence newer than the last one seen on this stream (or no sequence given)
    stream_changed: bool = False  # this reading switched stream; the baseline restarted
    advanced: bool = False  # this reading moved PTS forward: the only proof that the picture is live
    generation: int = 0  # counts stream switches; a reader that saw another value must restart its round
    pts: Optional[float] = None
    static_seconds: float = 0.0
    message: str = ""


class FrameStream:
    """Freshness state for one device. Thread-safe: readings come from workers and the event loop."""

    def __init__(
        self,
        device_id: str,
        freeze_threshold: float = FREEZE_THRESHOLD_SEC,
        clock: Callable[[], float] = time.monotonic,
        require_pts: bool = True,
        min_increment: float = 1e-6,
    ) -> None:
        self.device_id = str(device_id)
        self.freeze_threshold = float(freeze_threshold)
        self.clock = clock
        self.require_pts = bool(require_pts)
        self.min_increment = float(min_increment)
        self._lock = threading.Lock()
        self._stream_id: Optional[str] = None
        self._sequence: Optional[int] = None
        self._pts: Optional[float] = None
        self._advanced_at: Optional[float] = None  # clock time PTS last advanced on this stream
        self._advanced = False  # the last reading moved PTS forward (a first frame does not count)
        self._generation = 0
        self._verdict: Optional[Verdict] = None
        self._status_frozen = False  # stays frozen until PTS advances on this device

    @property
    def is_frozen(self) -> bool:
        verdict = self._verdict
        return verdict is not None and verdict.kind == FROZEN

    def frozen_now(self, now: Optional[float] = None) -> bool:
        """Status stays frozen until PTS advances. Reads without the lock and takes no sample."""
        return self._status_frozen

    def last_verdict(self) -> Optional[Verdict]:
        return self._verdict

    def last_advanced(self) -> bool:
        """The last reading moved PTS forward. A first frame or a static one does not prove liveness."""
        verdict = self._verdict
        return verdict is not None and verdict.advanced

    def observe(self, stream_id: Optional[str], sequence: Optional[int], pts: Optional[float],
                now: Optional[float] = None) -> Verdict:
        """Judge one reading of the newest frame."""
        with self._lock:
            now = self.clock() if now is None else float(now)
            changed = stream_id != self._stream_id
            if changed:
                # PTS only compares within one stream: a new stream restarts the baseline.
                self._stream_id = stream_id
                self._generation += 1
                self._sequence = None
                self._pts = None
                self._advanced_at = None
            fresh = sequence is None or self._sequence is None or sequence > self._sequence
            if fresh and sequence is not None:
                self._sequence = sequence

            if pts is None:
                self._advanced = False
                if not self.require_pts:
                    return self._record(Verdict(LIVE, "pts_not_required", now, fresh, changed))
                return self._record(Verdict(FROZEN, "missing_pts", now, fresh, changed,
                                            message=MISSING_PTS_MESSAGE))

            pts = float(pts)
            if self._pts is None:
                # First PTS of a stream is its baseline; nothing to compare yet.
                self._pts = pts
                self._advanced_at = now
                self._advanced = False
                return self._record(Verdict(LIVE, "first_frame", now, fresh, changed, pts=pts))
            if pts > self._pts + self.min_increment:
                self._pts = pts
                self._advanced_at = now
                self._advanced = True
                return self._record(Verdict(LIVE, "advanced", now, fresh, changed, advanced=True, pts=pts))

            # PTS did not advance (static or retrograde).
            self._advanced = False
            static = max(0.0, now - self._advanced_at)
            if static >= self.freeze_threshold:
                return self._record(Verdict(
                    FROZEN, "timestamp_static", now, fresh, changed, pts=pts, static_seconds=static,
                    message=f"Frame presentation timestamp static for {static:.2f}s "
                            f"(threshold {self.freeze_threshold:g}s)"))
            return self._record(Verdict(PENDING, "timestamp_not_advanced", now, fresh, changed,
                                        pts=pts, static_seconds=static))

    def observe_snapshot(self, snapshot: Optional["FrameSnapshot"], now: Optional[float] = None) -> Verdict:
        """Judge the newest frame as the KVM handed it out; ``None`` means no picture yet."""
        if snapshot is None:
            return self.observe_no_frame(now)
        return self.observe(snapshot.stream_id, snapshot.sequence, snapshot.pts, now)

    def observe_no_frame(self, now: Optional[float] = None) -> Verdict:
        """No picture at all: frozen, with the stream's baseline kept."""
        with self._lock:
            now = self.clock() if now is None else float(now)
            self._advanced = False
            return self._record(Verdict(FROZEN, "no_frame", now, message=NO_FRAME_MESSAGE))

    async def wait_live(self, read: Callable[[], Optional["FrameSnapshot"]], poll_interval: float = 0.05,
                        sleep: Callable[[float], object] = asyncio.sleep) -> Verdict:
        """Active window for a one-shot command: ends on a PTS advance (LIVE) or FROZEN.

        When the last reading advanced PTS within the threshold, that is enough: no new read.
        Otherwise read until PTS advances (LIVE), the threshold passes (FROZEN), or two thresholds
        pass with no advance at all (FROZEN; a stream that keeps changing stream id never freezes by PTS).
        """
        with self._lock:
            start = self.clock()
            recent_advance = (
                self._advanced
                and self._advanced_at is not None
                and start - self._advanced_at < self.freeze_threshold
                and self._verdict is not None
            )
            if recent_advance:
                return self._verdict
        deadline = start + 2 * self.freeze_threshold
        while True:
            verdict = self.observe_snapshot(read())
            if verdict.advanced or verdict.kind == FROZEN or verdict.reason == "pts_not_required":
                return verdict
            if self.clock() >= deadline:
                with self._lock:
                    return self._record(Verdict(
                        FROZEN, "window_expired", self.clock(),
                        message=f"No Presentation Timestamp advance within {2 * self.freeze_threshold:g}s"))
            await sleep(poll_interval)

    def _record(self, verdict: Verdict) -> Verdict:
        if verdict.kind == FROZEN:
            self._status_frozen = True
        elif verdict.advanced:
            self._status_frozen = False
        self._verdict = replace(verdict, generation=self._generation)
        return self._verdict
