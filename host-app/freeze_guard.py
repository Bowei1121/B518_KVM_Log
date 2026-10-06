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
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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


@dataclass
class DevicePTSState:
    """Tracks PTS history and freeze state for a specific device or station."""
    device_id: str
    last_pts: Optional[float] = None
    first_static_time: Optional[float] = None
    last_observed_time: Optional[float] = None
    last_sequence: Optional[int] = None
    observation_count: int = 0
    is_frozen: bool = False
    history: List[Tuple[float, float]] = field(default_factory=list)  # [(clock_time, pts), ...]


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

    def observe(
        self,
        device_id: str,
        pts: Optional[float],
        now: Optional[float] = None,
        sequence: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record an incoming frame's presentation timestamp.
        
        Returns a decision dictionary:
            - If live / advancing: {"is_frozen": False, "status": "ok", ...}
            - If static within threshold: {"is_frozen": False, "status": "warning", ...}
            - If frozen (static >= threshold): {"is_frozen": True, "status": "error", "error": "frame_frozen", ...}
        """
        now = self.clock() if now is None else float(now)
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

    async def verify_liveness(
        self,
        kvm: Any,
        device_id: Optional[str] = None,
        threshold_window: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Verify that the KVM source is live and its PTS advances over the inspection window.
        
        Fails fast with {"status": "error", "error": "frame_frozen"} if timestamps
        remain static for the designated threshold window.
        
        Returns:
            (True, None) if frame PTS increments normally.
            (False, error_dict) if video stream is frozen or missing PTS.
        """
        target_threshold = (
            float(threshold_window)
            if threshold_window is not None
            else self.freeze_threshold
        )
        dev_key = str(device_id) if device_id is not None else "default"

        pts_start = extract_pts(kvm)
        seq_start = extract_frame_sequence(kvm)
        now_start = self.clock()

        if pts_start is None:
            if self.require_pts:
                err_resp = {
                    "status": "error",
                    "error": "frame_frozen",
                    "reason": "missing_pts",
                    "message": "Missing Presentation Timestamp on KVM frame",
                    "device": dev_key,
                }
                self.observe(dev_key, None, now=now_start, sequence=seq_start)
                return False, err_resp
            return True, None

        state = self.get_state(dev_key)

        # If we have observed an earlier PTS on this device and the current PTS has
        # ALREADY advanced past it, the stream is confirmed live without waiting!
        if (
            state.last_pts is not None
            and pts_start > state.last_pts + self.min_increment
            and (now_start - (state.first_static_time or now_start)) < target_threshold
        ):
            self.observe(dev_key, pts_start, now=now_start, sequence=seq_start)
            return True, None

        # Record this initial observation
        obs = self.observe(dev_key, pts_start, now=now_start, sequence=seq_start)
        if obs.get("is_frozen"):
            return False, {
                "status": "error",
                "error": "frame_frozen",
                "reason": obs.get("reason", "timestamp_static"),
                "pts": pts_start,
                "message": obs.get("message", "Frame presentation timestamp is static"),
                "device": dev_key,
            }

        # Inspection window loop: actively poll KVM for PTS progression
        elapsed = 0.0
        while elapsed < target_threshold:
            await asyncio.sleep(poll_interval)
            now_current = self.clock()
            elapsed = now_current - now_start

            pts_current = extract_pts(kvm)
            seq_current = extract_frame_sequence(kvm)

            if pts_current is not None and pts_current > pts_start + self.min_increment:
                # Presentation timestamp has advanced! Stream is actively live.
                self.observe(dev_key, pts_current, now=now_current, sequence=seq_current)
                logger.debug(
                    "FreezeGuard: device %s live (PTS advanced %.4f -> %.4f in %.3fs)",
                    dev_key,
                    pts_start,
                    pts_current,
                    elapsed,
                )
                return True, None

        # Threshold window expired without any PTS advancement -> Frame is FROZEN!
        now_end = self.clock()
        final_obs = self.observe(dev_key, pts_start, now=now_end, sequence=seq_start)
        logger.warning(
            "FreezeGuard: device %s detected as FROZEN! PTS=%.4f did not advance within %.2fs threshold",
            dev_key,
            pts_start,
            target_threshold,
        )
        return False, {
            "status": "error",
            "error": "frame_frozen",
            "reason": "timestamp_static",
            "pts": pts_start,
            "static_seconds": round(now_end - now_start, 3),
            "threshold_seconds": target_threshold,
            "message": f"Frame presentation timestamp did not advance within {round(target_threshold, 2)}s window",
            "device": dev_key,
        }

    def verify_liveness_sync(
        self,
        kvm: Any,
        device_id: Optional[str] = None,
        threshold_window: Optional[float] = None,
        poll_interval: float = 0.05,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Synchronous version of verify_liveness for non-async flows or unittests."""
        target_threshold = (
            float(threshold_window)
            if threshold_window is not None
            else self.freeze_threshold
        )
        dev_key = str(device_id) if device_id is not None else "default"

        pts_start = extract_pts(kvm)
        seq_start = extract_frame_sequence(kvm)
        now_start = self.clock()

        if pts_start is None:
            if self.require_pts:
                self.observe(dev_key, None, now=now_start, sequence=seq_start)
                return False, {
                    "status": "error",
                    "error": "frame_frozen",
                    "reason": "missing_pts",
                    "message": "Missing Presentation Timestamp on KVM frame",
                    "device": dev_key,
                }
            return True, None

        state = self.get_state(dev_key)
        if (
            state.last_pts is not None
            and pts_start > state.last_pts + self.min_increment
            and (now_start - (state.first_static_time or now_start)) < target_threshold
        ):
            self.observe(dev_key, pts_start, now=now_start, sequence=seq_start)
            return True, None

        obs = self.observe(dev_key, pts_start, now=now_start, sequence=seq_start)
        if obs.get("is_frozen"):
            return False, {
                "status": "error",
                "error": "frame_frozen",
                "reason": obs.get("reason", "timestamp_static"),
                "pts": pts_start,
                "message": obs.get("message", "Frame presentation timestamp is static"),
                "device": dev_key,
            }

        elapsed = 0.0
        while elapsed < target_threshold:
            time.sleep(poll_interval)
            now_current = self.clock()
            elapsed = now_current - now_start

            pts_current = extract_pts(kvm)
            seq_current = extract_frame_sequence(kvm)

            if pts_current is not None and pts_current > pts_start + self.min_increment:
                self.observe(dev_key, pts_current, now=now_current, sequence=seq_current)
                return True, None

        now_end = self.clock()
        self.observe(dev_key, pts_start, now=now_end, sequence=seq_start)
        return False, {
            "status": "error",
            "error": "frame_frozen",
            "reason": "timestamp_static",
            "pts": pts_start,
            "static_seconds": round(now_end - now_start, 3),
            "threshold_seconds": target_threshold,
            "message": f"Frame presentation timestamp did not advance within {round(target_threshold, 2)}s window",
            "device": dev_key,
        }

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
