"""Shared fake KVM constructors for the tests.

Every fake serves ``latest_frame()`` the way ``JetKVMClient`` does: a ``FrameSnapshot``, or None
before the first frame. Build fakes here instead of returning a hand-written tuple.
"""

from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from frame_snapshot import FrameSnapshot  # noqa: E402


def make_snapshot(image: Any = None, *, sequence: int = 1, received_monotonic: Optional[float] = None,
                  stream_id: str = "fake-stream", pts: Optional[float] = None) -> FrameSnapshot:
    """A snapshot as the client hands it out. ``received_monotonic`` defaults to now."""
    if received_monotonic is None:
        received_monotonic = time.monotonic()
    return FrameSnapshot(image, sequence, received_monotonic, stream_id, pts)


def beside(screen: Any, app_picture: Any) -> Any:
    """One picture: ``screen`` unchanged at the top-left, ``app_picture`` (a Log Solution render) to its right.

    The screen keeps its own coordinates, so flows that match templates on it behave as before, and the
    Log Solution reader still finds its window by the locators wherever it sits in the picture.
    """
    import numpy as np

    height = max(screen.shape[0], app_picture.shape[0])
    canvas = np.zeros((height, screen.shape[1] + app_picture.shape[1], 3), dtype=np.uint8)
    canvas[:screen.shape[0], :screen.shape[1]] = screen
    canvas[:app_picture.shape[0], screen.shape[1]:] = app_picture
    return canvas


class FakeKVMSession:
    """The client contract the pool relies on: connect, close, is_healthy.

    Health follows ``JetKVMClient.is_healthy``: it turns False when the video track ends
    (``end_track()``) or the peer connection goes to ``failed``/``closed`` (``set_peer_state()``).
    Connecting again starts a fresh session, as a new client instance would.
    """

    connected: bool = False
    track_ended: bool = False
    peer_state: str = "connected"

    async def connect(self) -> None:
        self.connected = True
        self.track_ended = False
        self.peer_state = "connected"

    async def close(self) -> None:
        self.connected = False

    def end_track(self) -> None:
        self.track_ended = True

    def set_peer_state(self, state: str) -> None:
        self.peer_state = state

    def is_healthy(self) -> bool:
        return self.connected and not self.track_ended and self.peer_state not in ("failed", "closed")

    def unhealthy_reason(self) -> str:
        if not self.connected:
            return "not_connected"
        if self.track_ended:
            return "track_ended"
        if self.peer_state in ("failed", "closed"):
            return "peer_" + self.peer_state
        return ""


class FakeKVM(FakeKVMSession):
    """Attribute-backed fake with the client's own field names (``frame``, ``frame_sequence``, ...).

    Tests drive it by assigning those attributes; ``latest_frame()`` copies them into a snapshot
    the way ``JetKVMClient`` does under its lock.
    """

    def __init__(self, frame: Any = None, *, sequence: int = 1, received_monotonic: Optional[float] = None,
                 stream_id: str = "fake-stream", pts: Optional[float] = None) -> None:
        self.frame = frame
        self.frame_sequence = sequence
        self.frame_received_monotonic = time.monotonic() if received_monotonic is None else received_monotonic
        self.stream_id = stream_id
        self.frame_presentation_time = pts
        self.connected = True

    def latest_frame(self) -> Optional[FrameSnapshot]:
        if self.frame is None:
            return None
        return make_snapshot(copy.deepcopy(self.frame), sequence=self.frame_sequence,
                             received_monotonic=self.frame_received_monotonic,
                             stream_id=self.stream_id, pts=self.frame_presentation_time)
