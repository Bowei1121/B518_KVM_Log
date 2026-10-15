"""Shared helpers for protocol 1.0 socket tests."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_registry import DeviceRegistry  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

PROTOCOL = "1.0"


def make_registry(routes=None) -> DeviceRegistry:
    """Registry with DFU-01..20, FCT-01..20, BT-01..20 on distinct IPs.

    ``routes`` overrides or adds entries (device ID -> {station, kvm_ip, channels}) for tests
    that need specific IPs. Tests address devices by these IDs.
    """
    data = {
        f"DFU-{i:02d}": {"station": "DFU", "kvm_ip": f"192.168.200.{i}", "channels": 4}
        for i in range(1, 21)
    }
    data.update({
        f"FCT-{i:02d}": {"station": "FCT", "kvm_ip": f"192.168.201.{i}", "channels": 4}
        for i in range(1, 21)
    })
    data.update({
        f"BT-{i:02d}": {"station": "BT", "kvm_ip": f"192.168.202.{i}", "channels": 4}
        for i in range(1, 21)
    })
    data.update(routes or {})
    return DeviceRegistry.from_text(json.dumps(data))


def wire(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Fill protocol_version and req_id when a test does not set them."""
    out = dict(obj)
    out.setdefault("protocol_version", PROTOCOL)
    out.setdefault("req_id", f"t-{uuid.uuid4().hex[:8]}")
    out.setdefault("device", "")
    return out


def numbered(device: Any) -> str:
    """Map legacy numeric test device names onto valid DFU-NN IDs."""
    return f"DFU-{int(device):02d}"


class ProbeServer(TcpJsonServer):
    """TcpJsonServer with a device-bound 'probe' command, used to exercise device locks."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.register_handler("probe", lambda req: {"pong": True}, device_bound=True)


class FakeKVM:
    """KVM client stub for tests that exercise dispatch without caring about frames."""

    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False


def make_pool() -> KVMConnectionPool:
    """Pool whose connections never touch the network."""
    return KVMConnectionPool(client_factory=lambda host, password="": FakeKVM())


class LogSolutionKVM:
    """KVM stub whose latest_frame() is a synthetic Log Solution screen (contract 1.1).

    Each latest_frame() call advances the presentation timestamp unless frozen() was called.
    """

    def __init__(self, state="standby", statuses=()):
        import threading
        self._lock = threading.Lock()
        self._state = None
        self._statuses = []
        self.show(state, statuses)
        self._seq = 0
        self._pts = 100.0
        self._frozen = False
        self.connected = False

    def show(self, state, statuses=()):
        """Show a state. Standby/monitoring/review screens default to four waiting slots."""
        if not statuses and state in ("standby", "monitoring", "review"):
            statuses = ["WAITING"] * 4
        with self._lock:
            self._state = state
            self._statuses = list(statuses)

    def blank(self):
        with self._lock:
            self._state = None

    def freeze(self):
        with self._lock:
            self._frozen = True

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    def latest_frame(self):
        import time as _time
        with self._lock:
            self._seq += 1
            if not self._frozen:
                self._pts += 0.033
            frame = render_log_solution(self._state, self._statuses)
            return (frame, self._seq, _time.monotonic(), "synthetic-stream", self._pts)


def make_log_solution_pool(kvm: "LogSolutionKVM") -> KVMConnectionPool:
    """Pool whose every connection is the given synthetic KVM."""
    return KVMConnectionPool(client_factory=lambda host, password="": kvm)


_LOG_COLORS = {
    "PASS": (0, 239, 0), "FAIL": (0, 0, 255), "TESTING": (0, 255, 255),
    "NOTEST": (240, 75, 240), "WAITING": (217, 217, 217),
    "COMPLETING": (255, 199, 130), "STOPPED": (191, 191, 191),
    "TIMEOUT": (0, 153, 255),
}


def render_log_solution(state, statuses, scale=2, origin=(23, 31)):
    """Render contract 1.1 (App layout). state None renders a blank screen."""
    import numpy as np
    from round_frame_consumer import MarkerState

    patterns = {
        MarkerState.STANDBY: (1, 0, 0, 1),
        MarkerState.MONITORING: (1, 1, 0, 0),
        MarkerState.REVIEW: (1, 0, 1, 0),
        MarkerState.COMPLETE: (0, 1, 1, 0),
    }
    width, height = round(376 * scale), round(110 * scale)
    ox, oy = origin
    frame = np.full((height + oy, width + ox, 3), (245, 246, 247), dtype=np.uint8)
    if state is None:
        return frame

    def rect(x, y, w, h, color):
        x0, y0 = round(ox + x * scale), round(oy + y * scale)
        x1, y1 = round(ox + (x + w) * scale), round(oy + (y + h) * scale)
        frame[y0:y1, x0:x1] = color

    for x, inset in ((82, 3), (320, 11)):
        rect(x, 2, 22, 22, (0, 0, 0))
        rect(x + inset, 2 + inset, 8, 8, (255, 255, 255))
    for index, black in enumerate(patterns[MarkerState(state)]):
        row, col = divmod(index, 2)
        rect(278 + 2 + col * 12, 2 + row * 12, 10, 10, (0, 0, 0) if black else (255, 255, 255))
    rows = 2 if len(statuses) > 10 else 1
    rect(130, 2, 26, 14, (255, 255, 255))
    for index, black in enumerate((rows == 1, rows == 2)):
        rect(132 + index * 12, 4, 10, 10, (0, 0, 0) if black else (255, 255, 255))
    for slot in range(1, rows * 10 + 1):
        row, col = divmod(slot - 1, 10)
        status = statuses[slot - 1] if slot <= len(statuses) else "OUTSIDE"
        color = (0, 0, 0) if status == "OUTSIDE" else _LOG_COLORS[status]
        rect(col * 34, 34 + row * 27, 34, 26, color)
    return frame
