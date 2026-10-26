"""Per-device FrameStream registry: the server's seam for picture freshness (ADR-0005).

The freeze rule itself lives in frame_stream.FrameStream. This module only hands out one
FrameStream per device and summarises their latched frozen states for status.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict

from frame_stream import FREEZE_THRESHOLD_SEC, FrameStream


class FrameFreezeGuard:
    """Owns each device's FrameStream and the shared freeze threshold."""

    def __init__(
        self,
        freeze_threshold: float = FREEZE_THRESHOLD_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.freeze_threshold = float(freeze_threshold)
        self.clock = clock
        self._streams: Dict[str, FrameStream] = {}

    def stream(self, device_id: Any) -> FrameStream:
        """The device's FrameStream, created on first use."""
        key = str(device_id)
        stream = self._streams.get(key)
        if stream is None:
            stream = self._streams.setdefault(
                key, FrameStream(key, freeze_threshold=self.freeze_threshold, clock=self.clock))
        return stream

    def get_status(self) -> Dict[str, Any]:
        """Devices stay frozen in status until PTS advances, including after a stream switch."""
        now = self.clock()
        streams = list(self._streams.items())
        frozen_devices = [key for key, stream in streams if stream.frozen_now(now)]
        return {
            "freeze_threshold": self.freeze_threshold,
            "require_pts": True,  # the server always requires PTS (ADR-0005 2026-10-20 第 2 點)
            "tracked_devices_count": len(streams),
            "frozen_devices_count": len(frozen_devices),
            "frozen_devices": frozen_devices,
        }
