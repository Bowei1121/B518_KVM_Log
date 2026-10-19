"""JetKVMClient health: only the two death signals count (video track ended, peer connection failed/closed).

Real client, no network: a fake track and a fake frame stand in for aiortc's.
"""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from aiortc.mediastreams import MediaStreamError  # noqa: E402
from jetkvm_core import JetKVMClient  # noqa: E402


class _Frame:
    width = 4
    height = 2
    pts = 1
    time_base = 0.5

    def to_ndarray(self, format):
        return np.zeros((2, 4, 3), dtype=np.uint8)


class _Track:
    """Yields its frames, then ends the way aiortc does (recv() raises MediaStreamError).

    With ``stall=True`` it goes silent instead of ending.
    """

    def __init__(self, frames, stall=False):
        self._frames = list(frames)
        self._stall = stall

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        if self._stall:
            await asyncio.sleep(3600)
        raise MediaStreamError


def _connected_client():
    client = JetKVMClient("192.168.1.10")
    client.connected = True
    return client


def test_not_healthy_before_connect():
    assert JetKVMClient("192.168.1.10").is_healthy() is False


def test_healthy_once_connected():
    assert _connected_client().is_healthy() is True


def test_video_track_end_is_death_and_keeps_the_last_picture():
    client = _connected_client()

    asyncio.run(client._pump_video(_Track([_Frame()])))

    assert client.is_healthy() is False
    assert client.frame is not None
    assert client.frame_sequence == 1


def test_stale_picture_is_not_death():
    client = _connected_client()

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(client._pump_video(_Track([_Frame()], stall=True)), timeout=0.1))
    client.frame_received_monotonic = time.monotonic() - 3600  # picture an hour old, track still open

    assert client.is_healthy() is True


@pytest.mark.parametrize("state, healthy", [
    ("new", True),
    ("connecting", True),
    ("connected", True),
    ("disconnected", True),   # transient ICE state, not death
    ("failed", False),
    ("closed", False),
])
def test_peer_connection_state_decides_health(state, healthy):
    client = _connected_client()

    client._on_peer_state(state)

    assert client.is_healthy() is healthy


def test_close_makes_client_unhealthy():
    client = _connected_client()

    asyncio.run(client.close())

    assert client.is_healthy() is False
