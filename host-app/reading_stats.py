"""Per-device statistics of Log Solution frame readings, reported by core_status (issue #94).

Every reading that decodes a frame records here once: check, inspect, the monitor flow, the HID pre-refusal
checks and the heartbeat probe. ``report()`` only returns what was recorded. It never reads a frame and
never takes the device lock, so core_status stays free of KVM I/O. Records come from worker threads
and the event loop reports, so every access holds the lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

from log_solution_reader import read_app_frame, read_snapshot

READING_WINDOW = 50  # readable_ratio covers the last this many readings


class ReadingStats:
    def __init__(self, window: int = READING_WINDOW) -> None:
        self._lock = threading.Lock()
        self._window: deque = deque(maxlen=window)  # reliable flag per reading, oldest first
        self._frame_size: Optional[Tuple[int, int]] = None  # (width, height) of the last recorded frame
        self._scale: float = 0.0  # scale of the last reading; 0.0 when the app was not located
        self._reason: str = ""  # reason of the last reading; "" when it was reliable
        self._at: Optional[float] = None  # monotonic time of the last reading
        self._frame_colorspace: Optional[str] = None  # the stream's own tag on the last recorded frame
        self._decode_colorspace: Optional[str] = None  # the YUV->BGR matrix Core applied to it

    def record(self, observation: Any, frame: Any, now: Optional[float] = None,
               frame_colorspace: Optional[str] = None, decode_colorspace: Optional[str] = None) -> None:
        """Count one decoded reading. ``frame`` is the BGR image the observation was read from.

        The two colorspace names come from the snapshot (None when the source does not know them).
        """
        now = time.monotonic() if now is None else now
        shape = getattr(frame, "shape", None)
        reliable = bool(observation.reliable)
        with self._lock:
            if shape is not None and len(shape) >= 2:
                self._frame_size = (int(shape[1]), int(shape[0]))
            self._window.append(reliable)
            self._reason = "" if reliable else str(observation.reason)
            self._scale = float(observation.scale or 0.0)
            self._at = now
            # A source without the metadata (replay, tests) reports null rather than a stale value.
            self._frame_colorspace = frame_colorspace
            self._decode_colorspace = decode_colorspace

    def report(self, now: Optional[float] = None) -> Dict[str, Any]:
        """The per-device reading fields of core_status (five reading fields plus the two colorspace names). Nothing read yet: every value is null, samples 0."""
        now = time.monotonic() if now is None else now
        with self._lock:
            samples = len(self._window)
            readable = sum(self._window)
            frame_size = self._frame_size
            scale = self._scale
            reason = self._reason
            at = self._at
            frame_colorspace = self._frame_colorspace
            decode_colorspace = self._decode_colorspace
        picture_scale = round(scale, 3) if scale > 0 else None
        return {
            "frame_size": None if frame_size is None else [frame_size[0], frame_size[1]],
            "picture_scale": picture_scale,
            "cell_px": None if picture_scale is None else round(picture_scale * 10, 3),
            "last_reading": None if at is None else {"reason": reason, "age_sec": round(now - at, 2)},
            "readable_ratio": {"ratio": round(readable / samples, 3) if samples else None, "samples": samples},
            "frame_colorspace": frame_colorspace,
            "decode_colorspace": decode_colorspace,
        }


def empty_report() -> Dict[str, Any]:
    """core_status fields of a device with no recorded reading (a fresh ReadingStats)."""
    return ReadingStats().report()


def read_app_frame_recorded(kvm: Any, stats: Optional[ReadingStats]) -> Tuple[Any, Any]:
    """``read_app_frame(kvm)`` that records the reading: ``(snapshot, observation)``."""
    snapshot, observation = read_app_frame(kvm)
    if stats is not None and snapshot is not None:
        stats.record(observation, snapshot.image,
                     frame_colorspace=getattr(snapshot, "frame_colorspace", None),
                     decode_colorspace=getattr(snapshot, "decode_colorspace", None))
    return snapshot, observation


def read_latest_app_frame_recorded(kvm: Any, stats: Optional[ReadingStats]) -> Tuple[Any, Any]:
    """``read_latest_app_frame(kvm)`` that records the reading: ``(frame, observation)``."""
    snapshot, observation = read_app_frame_recorded(kvm, stats)
    return (None if snapshot is None else snapshot.image), observation


def read_snapshot_recorded(snapshot: Any, stats: Optional[ReadingStats]) -> Any:
    """``read_snapshot(snapshot)`` that records the reading. Used as the heartbeat probe's decode."""
    observation = read_snapshot(snapshot)
    if stats is not None and observation is not None:
        stats.record(observation, snapshot.image,
                     frame_colorspace=getattr(snapshot, "frame_colorspace", None),
                     decode_colorspace=getattr(snapshot, "decode_colorspace", None))
    return observation
