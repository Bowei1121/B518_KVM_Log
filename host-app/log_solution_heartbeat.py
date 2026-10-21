"""Log Solution heartbeat watch (issue #77, C8): does the App's display still move?

The 2x2 heartbeat beside the state marker counts every 0.5 s while the App is alive. The JetKVM stream
keeps sending PTS when the Mac or Log Solution hangs, so FrameStream cannot tell that apart. This module
only watches whether the four cells change between counted observations. It never decodes the counter.

A counted observation is a new frame (sequence differs from the last counted one), with all four cells
readable, at least STEP_MIN_SEC after the last counted one. Three counted observations in a row with the
same cells mean the display stopped. A gap above STEP_MAX_SEC restarts the run. Any invalid observation
(cells unreadable, frozen picture, stale frame, new stream) clears the run and the last-change memory.
Nothing here locks: the next counted change is normal again.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from enum import Enum
from typing import Any, Callable, Optional, Tuple

HEARTBEAT_STOPPED = "log_solution_heartbeat_stopped"
HEARTBEAT_STOPPED_MESSAGE = "畫面心跳停止，請人員檢查測試電腦與 Log Solution"
HEARTBEAT_UNREADABLE = "heartbeat_unreadable"  # reason of a reading whose heartbeat cells are not all readable

STEP_MIN_SEC = 0.8  # counted observations are at least this far apart (heartbeat step is 0.5 s)
STEP_MAX_SEC = 3.0  # a longer gap restarts the run
STOPPED_RUN = 3  # this many identical counted observations in a row
RECENT_CHANGE_SEC = 3.0  # a change seen this recently lets a HID command through without sampling
PROBE_MAX_SEC = 2.5  # sampling limit before a HID command when no recent change was seen
PROBE_INTERVAL_SEC = 0.05

Cells = Tuple[bool, bool, bool, bool]


class Step(Enum):
    NOT_COUNTED = "not_counted"  # repeated or older frame, or under STEP_MIN_SEC: the run is untouched
    COUNTED = "counted"  # first counted observation, or the same cells as the previous one
    CHANGED = "changed"  # the cells differ from the previous counted observation
    STOPPED = "stopped"  # the last STOPPED_RUN counted observations are identical
    INVALID = "invalid"  # cells unreadable: the run is cleared


def cells_of(observation: Any) -> Optional[Cells]:
    """The four heartbeat cells as booleans, or None when any is unreadable or the reading has none."""
    cells = tuple(getattr(observation, "heartbeat", ()))
    if len(cells) != 4 or any(cell is None for cell in cells):
        return None
    return tuple(bool(cell) for cell in cells)


def unreadable_heartbeat(observation: Any) -> Any:
    """A reliable reading whose heartbeat is not fully readable is Log Solution not visible, not a marker reading."""
    if observation.reliable and cells_of(observation) is None:
        return replace(observation, reliable=False, reason=HEARTBEAT_UNREADABLE)
    return observation


class HeartbeatTracker:
    """One device's heartbeat run. Thread-safe: readings come from workers and from the HID probe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream_id: Optional[str] = None
        self._sequence: Optional[int] = None  # sequence of the last counted observation
        self._received_at: Optional[float] = None  # received time of the last counted observation
        self._run: list[Cells] = []  # cells of the last counted observations, at most STOPPED_RUN
        self._changed_at: Optional[float] = None  # received time of the last counted change

    def observe(self, stream_id: Optional[str], sequence: Optional[int], received_at: float,
                cells: Optional[Cells]) -> Step:
        """Feed one reading. ``cells`` None means the reading has no usable heartbeat."""
        with self._lock:
            if cells is None:
                self._clear()
                return Step.INVALID
            if stream_id != self._stream_id:
                self._clear()
                self._stream_id = stream_id
            elif sequence is not None and self._sequence is not None and sequence <= self._sequence:
                return Step.NOT_COUNTED
            if self._received_at is not None:
                gap = received_at - self._received_at
                if gap < STEP_MIN_SEC:
                    return Step.NOT_COUNTED
                if gap > STEP_MAX_SEC:
                    self._run = []
            self._sequence = sequence
            self._received_at = received_at
            previous = self._run[-1] if self._run else None
            self._run = (self._run + [cells])[-STOPPED_RUN:]
            if previous is not None and cells != previous:
                self._changed_at = received_at
                return Step.CHANGED
            if len(self._run) == STOPPED_RUN and all(seen == cells for seen in self._run):
                return Step.STOPPED
            return Step.COUNTED

    def changed_within(self, now: float, window: float = RECENT_CHANGE_SEC) -> bool:
        """A counted change was seen at most ``window`` seconds ago and no invalid reading came since."""
        with self._lock:
            return self._changed_at is not None and 0 <= now - self._changed_at <= window

    def reset(self) -> None:
        """Drop the run and the last-change memory: the picture was frozen, stale, or gone."""
        with self._lock:
            self._clear()

    def _clear(self) -> None:
        self._sequence = None
        self._received_at = None
        self._run = []
        self._changed_at = None


def probe(tracker: HeartbeatTracker, read: Callable[[], Tuple[Any, Any]], *,
          clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Any] = time.sleep) -> bool:
    """Sample the picture for up to PROBE_MAX_SEC before a HID command. True when the display is stopped.

    ``read`` returns ``(snapshot, observation)`` as log_solution_reader.read_app_frame does. A change or an
    unreadable reading ends the probe as not stopped: the command goes on, and the existing Log Solution
    rules (Dock recovery, state refusals) decide the rest. Only three identical counted readings refuse.
    """
    deadline = clock() + PROBE_MAX_SEC
    while True:
        snapshot, observation = read()
        if snapshot is None:
            tracker.reset()
            return False
        step = tracker.observe(snapshot.stream_id, snapshot.sequence, snapshot.received_monotonic,
                               cells_of(observation))
        if step is Step.STOPPED:
            return True
        if step in (Step.CHANGED, Step.INVALID):
            return False
        if clock() >= deadline:
            return False
        sleep(PROBE_INTERVAL_SEC)
