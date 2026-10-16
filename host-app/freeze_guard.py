"""Frame Freeze Guard Across All Stations (BT, FCT, DFU).

Verifies that the Presentation Timestamp (PTS) of incoming WebRTC frames
advances over an inspection window. If the video stream is frozen
(timestamps remain static), checks fail fast with:
    {"status": "error", "error": "frame_frozen"}
preventing crashed ATE machines displaying static PASS screens from
generating false-positive test results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("FrameFreezeGuard")


class StationType(str, Enum):
    """Supported ATE station types."""
    BT = "BT"
    FCT = "FCT"
    DFU = "DFU"


def extract_pts(kvm_or_frame: Any) -> Optional[float]:
    """Extract Presentation Timestamp (PTS) in seconds from a KVM client or frame.

    Inspects:
    1. Tuple/list from kvm.latest_frame(): (frame, sequence, recv_time, stream_id, pts)
    2. Object method: kvm.latest_frame()
    3. Direct attributes on kvm: frame_presentation_time, presentation_time, pts, timestamp
    4. Direct attributes on frame: pts, timestamp, presentation_time
    """
    if kvm_or_frame is None:
        return None

    # 1. Tuple/list from latest_frame()
    if isinstance(kvm_or_frame, (tuple, list)):
        if len(kvm_or_frame) >= 5 and kvm_or_frame[4] is not None:
            try:
                return float(kvm_or_frame[4])
            except (ValueError, TypeError):
                pass
        return None

    # 2. Object with callable latest_frame()
    if hasattr(kvm_or_frame, "latest_frame") and callable(kvm_or_frame.latest_frame):
        try:
            snapshot = kvm_or_frame.latest_frame()
            if snapshot is not None and isinstance(snapshot, (tuple, list)) and len(snapshot) >= 5:
                if snapshot[4] is not None:
                    return float(snapshot[4])
        except Exception:
            pass

    # 3. Direct attributes on KVM or frame object
    for attr in ("frame_presentation_time", "presentation_time", "pts", "timestamp"):
        val = getattr(kvm_or_frame, attr, None)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue

    # 4. Check nested .frame if it has attributes
    inner_frame = getattr(kvm_or_frame, "frame", None)
    if inner_frame is not None and inner_frame is not kvm_or_frame:
        for attr in ("pts", "timestamp", "presentation_time"):
            val = getattr(inner_frame, attr, None)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue

    return None


def extract_frame_sequence(kvm_or_frame: Any) -> Optional[int]:
    """Extract frame sequence / serial number if available."""
    if kvm_or_frame is None:
        return None
    if isinstance(kvm_or_frame, (tuple, list)) and len(kvm_or_frame) >= 2:
        try:
            return int(kvm_or_frame[1])
        except (ValueError, TypeError):
            pass
    for attr in ("frame_sequence", "sequence", "seq"):
        val = getattr(kvm_or_frame, attr, None)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def extract_stream_id(kvm_or_frame: Any) -> Optional[str]:
    """Extract the stream id (index 3 of latest_frame()) so PTS is compared within one stream."""
    if kvm_or_frame is None:
        return None
    if isinstance(kvm_or_frame, (tuple, list)):
        if len(kvm_or_frame) >= 5 and kvm_or_frame[3] is not None:
            return str(kvm_or_frame[3])
        return None
    if hasattr(kvm_or_frame, "latest_frame") and callable(kvm_or_frame.latest_frame):
        try:
            snapshot = kvm_or_frame.latest_frame()
        except Exception:
            snapshot = None
        if isinstance(snapshot, (tuple, list)) and len(snapshot) >= 5 and snapshot[3] is not None:
            return str(snapshot[3])
    val = getattr(kvm_or_frame, "stream_id", None)
    return None if val is None else str(val)


def _read_meta(kvm: Any) -> Tuple[Optional[float], Optional[int], Optional[str]]:
    """(pts, sequence, stream_id) from one latest_frame() call when the KVM returns a snapshot."""
    snapshot = None
    if hasattr(kvm, "latest_frame") and callable(kvm.latest_frame):
        try:
            snapshot = kvm.latest_frame()
        except Exception:
            snapshot = None
    if isinstance(snapshot, (tuple, list)) and len(snapshot) >= 5:
        return extract_pts(snapshot), extract_frame_sequence(snapshot), extract_stream_id(snapshot)
    return extract_pts(kvm), extract_frame_sequence(kvm), extract_stream_id(kvm)


@dataclass(frozen=True)
class FrameMeta:
    """One reading of a KVM's latest frame, stamped with the guard's clock."""
    pts: Optional[float]
    sequence: Optional[int]
    stream_id: Optional[str]
    now: float


@dataclass
class DevicePTSState:
    """Tracks PTS history and freeze state for a specific device or station."""
    device_id: str
    last_pts: Optional[float] = None
    first_static_time: Optional[float] = None
    last_observed_time: Optional[float] = None
    last_sequence: Optional[int] = None
    stream_id: Optional[str] = None
    observation_count: int = 0
    is_frozen: bool = False
    history: List[Tuple[float, float]] = field(default_factory=list)  # [(clock_time, pts), ...]


class _FreezeWindow:
    """One inspection window: the device's PTS must advance past the baseline within the threshold."""

    def __init__(self, guard: "FrameFreezeGuard", device_id: str, threshold: float, baseline: FrameMeta) -> None:
        self._guard = guard
        self._device_id = device_id
        self._threshold = threshold
        self._baseline = baseline

    def expired(self, now: float) -> bool:
        return now - self._baseline.now >= self._threshold

    def step(self, frame: FrameMeta) -> bool:
        """Judge one later reading. True when its PTS advanced past the baseline within the same stream."""
        if self._guard._follow_stream(self._device_id, frame):
            # Reconnected mid-window: the new stream's first frame restarts the window, PTS or not.
            self._baseline = frame
            return False
        if self._baseline.pts is None:
            # Stream without PTS yet: the first frame that carries one becomes the baseline.
            if frame.pts is not None:
                self._baseline = frame
            return False
        if frame.pts is not None and frame.pts > self._baseline.pts + self._guard.min_increment:
            self._guard.observe(self._device_id, frame.pts, now=frame.now,
                                sequence=frame.sequence, stream_id=frame.stream_id)
            return True
        return False

    def frozen(self, now: float) -> Tuple[bool, Dict[str, Any]]:
        """Window expired without a PTS advance: the picture is frozen (or has no PTS at all)."""
        baseline = self._baseline
        self._guard.observe(self._device_id, baseline.pts, now=now,
                            sequence=baseline.sequence, stream_id=baseline.stream_id)
        if baseline.pts is None:
            return False, _missing_pts_error(self._device_id)
        logger.warning(
            "FreezeGuard: device %s detected as FROZEN! PTS=%.4f did not advance within %.2fs threshold",
            self._device_id, baseline.pts, self._threshold,
        )
        return False, {
            "status": "error",
            "error": "frame_frozen",
            "reason": "timestamp_static",
            "pts": baseline.pts,
            "static_seconds": round(now - baseline.now, 3),
            "threshold_seconds": self._threshold,
            "message": f"Frame presentation timestamp did not advance within {round(self._threshold, 2)}s window",
            "device": self._device_id,
        }


def _missing_pts_error(device_id: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "error": "frame_frozen",
        "reason": "missing_pts",
        "message": "Missing Presentation Timestamp on KVM frame",
        "device": device_id,
    }


class FrameFreezeGuard:
    """Presentation Timestamp (PTS) freeze guard for visual verification.

    Ensures that incoming WebRTC frames are actively advancing before
    confirming any visual inspection (BT, FCT, DFU).
    """

    def __init__(
        self,
        freeze_threshold: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        min_increment: float = 1e-6,
        require_pts: bool = True,
    ) -> None:
        self.freeze_threshold = float(freeze_threshold)
        self.clock = clock
        self.min_increment = float(min_increment)
        self.require_pts = bool(require_pts)
        self._states: Dict[str, DevicePTSState] = {}
        self._lock = asyncio.Lock()

    def reset(self, device_id: Optional[str] = None) -> None:
        """Reset tracking state for a specific device or all devices."""
        if device_id is not None:
            self._states.pop(str(device_id), None)
        else:
            self._states.clear()

    def get_state(self, device_id: str) -> DevicePTSState:
        """Get or initialize tracking state for a device."""
        key = str(device_id)
        if key not in self._states:
            self._states[key] = DevicePTSState(device_id=key)
        return self._states[key]

    def _bind_stream(self, device_id: str, stream_id: Optional[str]) -> None:
        """A new stream starts a new PTS baseline: the device's freeze state restarts from its first frame.

        PTS is only comparable within one stream. Without a stream id nothing is bound.
        A baseline observed before any stream id was known is adopted by the first stream seen.
        """
        if stream_id is None:
            return
        state = self.get_state(device_id)
        if state.stream_id is None or state.stream_id == stream_id:
            state.stream_id = stream_id
            return
        state.last_pts = None
        state.first_static_time = None
        state.last_sequence = None
        state.is_frozen = False
        state.history.clear()
        state.stream_id = stream_id

    def _follow_stream(self, device_id: str, frame: FrameMeta) -> bool:
        """Bind the device to the frame's stream. True when this switched the stream the device was on."""
        state = self.get_state(device_id)
        switched = (
            frame.stream_id is not None
            and state.stream_id is not None
            and state.stream_id != frame.stream_id
        )
        self._bind_stream(device_id, frame.stream_id)
        return switched

    def observe(
        self,
        device_id: str,
        pts: Optional[float],
        now: Optional[float] = None,
        sequence: Optional[int] = None,
        stream_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an incoming frame's presentation timestamp.

        Returns a decision dictionary:
            - If live / advancing: {"is_frozen": False, "status": "ok", ...}
            - If static within threshold: {"is_frozen": False, "status": "warning", ...}
            - If frozen (static >= threshold): {"is_frozen": True, "status": "error", "error": "frame_frozen", ...}
        """
        now = self.clock() if now is None else float(now)
        self._bind_stream(device_id, stream_id)
        state = self.get_state(device_id)
        state.observation_count += 1
        state.last_observed_time = now

        if sequence is not None:
            state.last_sequence = sequence

        # Missing timestamp handling
        if pts is None:
            if self.require_pts:
                state.is_frozen = True
                return {
                    "is_frozen": True,
                    "status": "error",
                    "error": "frame_frozen",
                    "reason": "missing_presentation_timestamp",
                    "message": "Missing Presentation Timestamp on frame",
                    "device": device_id,
                }
            return {
                "is_frozen": False,
                "status": "ok",
                "reason": "pts_not_required",
                "device": device_id,
            }

        pts = float(pts)
        state.history.append((now, pts))
        if len(state.history) > 20:
            state.history.pop(0)

        # First observation for this device
        if state.last_pts is None:
            state.last_pts = pts
            state.first_static_time = now
            state.is_frozen = False
            return {
                "is_frozen": False,
                "status": "initial",
                "reason": "first_frame",
                "pts": pts,
                "device": device_id,
            }

        # Check if PTS has strictly advanced
        if pts > state.last_pts + self.min_increment:
            # Active and advancing!
            state.last_pts = pts
            state.first_static_time = now
            state.is_frozen = False
            return {
                "is_frozen": False,
                "status": "ok",
                "reason": "incrementing",
                "pts": pts,
                "device": device_id,
            }

        # PTS did not advance (static or retrograde)
        static_start = state.first_static_time if state.first_static_time is not None else now
        static_duration = max(0.0, now - static_start)

        if static_duration >= self.freeze_threshold:
            state.is_frozen = True
            logger.warning(
                "FreezeGuard: device %s frame is FROZEN (PTS=%.4f static for %.2fs >= %.2fs)",
                device_id,
                pts,
                static_duration,
                self.freeze_threshold,
            )
            return {
                "is_frozen": True,
                "status": "error",
                "error": "frame_frozen",
                "reason": "timestamp_static",
                "pts": pts,
                "static_seconds": round(static_duration, 3),
                "threshold_seconds": self.freeze_threshold,
                "message": f"Frame presentation timestamp static for {round(static_duration, 2)}s",
                "device": device_id,
            }

        # Still within the threshold window but not yet confirmed frozen
        return {
            "is_frozen": False,
            "status": "warning",
            "reason": "timestamp_not_advanced",
            "pts": pts,
            "static_seconds": round(static_duration, 3),
            "threshold_seconds": self.freeze_threshold,
            "device": device_id,
        }

    def _read_frame(self, kvm: Any) -> FrameMeta:
        pts, sequence, stream_id = _read_meta(kvm)
        return FrameMeta(pts=pts, sequence=sequence, stream_id=stream_id, now=self.clock())

    def _open_window(
        self,
        device_id: str,
        stream_id: Optional[str],
        threshold: float,
        start: FrameMeta,
    ) -> Tuple[Optional[_FreezeWindow], Optional[Tuple[bool, Optional[Dict[str, Any]]]]]:
        """Start judging a device. Returns (window, None) to keep polling, or (None, verdict) when decided now."""
        self._bind_stream(device_id, stream_id)
        self._follow_stream(device_id, start)

        if start.pts is None:
            if self.require_pts:
                self.observe(device_id, None, now=start.now, sequence=start.sequence, stream_id=start.stream_id)
                return None, (False, _missing_pts_error(device_id))
            return None, (True, None)

        # If we have observed an earlier PTS on this device and the current PTS has
        # ALREADY advanced past it, the stream is confirmed live without waiting!
        state = self.get_state(device_id)
        if (
            state.last_pts is not None
            and start.pts > state.last_pts + self.min_increment
            and (start.now - (state.first_static_time or start.now)) < threshold
        ):
            self.observe(device_id, start.pts, now=start.now, sequence=start.sequence, stream_id=start.stream_id)
            return None, (True, None)

        obs = self.observe(device_id, start.pts, now=start.now, sequence=start.sequence, stream_id=start.stream_id)
        if obs.get("is_frozen"):
            return None, (False, {
                "status": "error",
                "error": "frame_frozen",
                "reason": obs.get("reason", "timestamp_static"),
                "pts": start.pts,
                "message": obs.get("message", "Frame presentation timestamp is static"),
                "device": device_id,
            })
        return _FreezeWindow(self, device_id, threshold, start), None

    async def verify_liveness(
        self,
        kvm: Any,
        device_id: Optional[str] = None,
        threshold_window: Optional[float] = None,
        poll_interval: float = 0.05,
        stream_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Verify that the KVM source is live and its PTS advances over the inspection window.

        ``stream_id`` is the stream the caller is checking; a frame from another stream restarts the device's freeze state.
        Fails fast with {"status": "error", "error": "frame_frozen"} if timestamps
        remain static for the designated threshold window.

        Returns:
            (True, None) if frame PTS increments normally.
            (False, error_dict) if video stream is frozen or missing PTS.
        """
        dev_key = str(device_id) if device_id is not None else "default"
        threshold = float(threshold_window) if threshold_window is not None else self.freeze_threshold

        window, verdict = self._open_window(dev_key, stream_id, threshold, self._read_frame(kvm))
        if verdict is not None:
            return verdict

        # Inspection window loop: actively poll KVM for PTS progression
        while not window.expired(self.clock()):
            await asyncio.sleep(poll_interval)
            if window.step(self._read_frame(kvm)):
                return True, None
        return window.frozen(self.clock())

    def verify_liveness_sync(
        self,
        kvm: Any,
        device_id: Optional[str] = None,
        threshold_window: Optional[float] = None,
        poll_interval: float = 0.05,
        stream_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Synchronous version of verify_liveness for non-async flows or unittests."""
        dev_key = str(device_id) if device_id is not None else "default"
        threshold = float(threshold_window) if threshold_window is not None else self.freeze_threshold

        window, verdict = self._open_window(dev_key, stream_id, threshold, self._read_frame(kvm))
        if verdict is not None:
            return verdict

        while not window.expired(self.clock()):
            time.sleep(poll_interval)
            if window.step(self._read_frame(kvm)):
                return True, None
        return window.frozen(self.clock())

    def get_status(self) -> Dict[str, Any]:
        """Return summary status of the FrameFreezeGuard."""
        frozen_devices = [
            k for k, state in self._states.items() if state.is_frozen
        ]
        return {
            "freeze_threshold": self.freeze_threshold,
            "require_pts": self.require_pts,
            "tracked_devices_count": len(self._states),
            "frozen_devices_count": len(frozen_devices),
            "frozen_devices": frozen_devices,
        }
