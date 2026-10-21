"""DeviceSlot: everything the server keeps for one device (issue #75, C2).

One slot per device ID, created on first use and kept while the server runs. It holds the device's
DeviceRound, the RoundFrameGate that shares it, the FrameStream from FrameFreezeGuard, the started-channel
tracker of inspect, and the in-flight reading of each lane. ``check`` (check_start and check_finish) shares
one lane, so only one of them reads a device at a time; ``inspect`` has its own. ``heartbeat`` is the device's
Log Solution heartbeat run (issue #77): check, inspect and the HID probe all feed the same one.
"""

from concurrent.futures import Future
from typing import Dict

from device_round import DeviceRound
from frame_stream import FrameStream
from log_solution_heartbeat import HeartbeatTracker
from log_solution_reader import ActivityTracker
from reading_stats import ReadingStats
from round_frame_consumer import RoundFrameGate


class DeviceSlot:
    def __init__(self, device_id: str, stream: FrameStream) -> None:
        self.device_id = str(device_id)
        self.stream = stream
        self.heartbeat = HeartbeatTracker()
        self.round = DeviceRound(self.device_id)
        self.stats = ReadingStats()
        self.gate = RoundFrameGate(self.device_id, require_presentation_time=True,
                                   round_state=self.round, frame_stream=stream, require_proof=True,
                                   heartbeat=self.heartbeat, stats=self.stats)
        self.inspect_tracker = ActivityTracker(max_gap=1.0)
        self.flights: Dict[str, Future] = {}
