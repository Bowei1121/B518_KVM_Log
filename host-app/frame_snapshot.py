"""The one shape a KVM hands out for its newest picture.

``JetKVMClient.latest_frame()`` and ``ReplayKVM.latest_frame()`` return this. Every reader
(Freeze Guard, Log Solution 讀取, TCP snapshot, HID flows) takes its fields by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class FrameSnapshot:
    """One decoded BGR picture plus the freshness metadata that travels with it."""

    image: Any                       # BGR numpy array, a copy owned by the reader; never None
    sequence: int                    # stream frame counter
    received_monotonic: float        # time.monotonic() when the frame arrived
    stream_id: str                   # identifies one WebRTC stream; PTS only compares within it
    pts: Optional[float]             # presentation timestamp in seconds, None when the stream sends none
