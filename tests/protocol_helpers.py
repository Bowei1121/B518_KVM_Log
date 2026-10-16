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


DOCK_HEIGHT = 48
DOCK_COLOR = (150, 150, 150)
# x, y, w, h of the Log Solution Dock icon inside the Dock strip (strip-relative).
DOCK_ICON_BOX = (40, 6, 36, 36)


class LogSolutionKVM:
    """KVM stub whose latest_frame() is a synthetic ATE screen with a Log Solution window and Dock strip.

    The Log Solution window (contract 1.1) sits at the top; the Dock strip below carries the
    Log Solution Dock icon as seeded noise (80-255), so template matching is exact and the icon
    cannot be mistaken for a locator square. Each latest_frame() call advances the presentation
    timestamp unless freeze() was called.

    Test switches:
    - cover(): the window is behind the ATE window (not drawn). A Dock click brings it back only
      when restore_on_dock is set; the state is kept.
    - close_window(): the window is not running. A Dock click reopens it in standby when restore_on_dock is set.
    - dock_visible / shortcut_enabled: hide the Dock icon, or make Cmd+Shift+M do nothing.
    - state_on_dock: state the window shows after a restoring Dock click, when set.
    - cover_left(until_x, colour, y_range): an ATE window covers the Log Solution window from its
      left edge up to ``until_x`` (contract units). A restoring Dock click clears it.
    - repeat_sequence: latest_frame() repeats the frame sequence number (a duplicate-frame stream problem).
    click(), move() and press_command_shift_m() are recorded in ``events`` as ("click", x, y),
    ("move", x, y) and ("key", name).
    """

    def __init__(self, state="standby", statuses=()):
        import threading
        import numpy as np
        self._lock = threading.Lock()
        self._state = None
        self._statuses = []
        self.show(state, statuses)
        self._seq = 0
        self._pts = 100.0
        self._frozen = False
        self._hidden = False
        self._dock_icon = np.random.RandomState(23).randint(
            80, 256, (DOCK_ICON_BOX[3], DOCK_ICON_BOX[2], 3), dtype=np.uint8)
        self.events = []
        self.dock_visible = True
        self.shortcut_enabled = True
        self.restore_on_dock = False
        self.state_on_dock = None
        self.repeat_sequence = False
        self._cover = None
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

    def cover(self):
        with self._lock:
            self._hidden = True

    def close_window(self):
        with self._lock:
            self._hidden = True
            self._state = None

    def cover_left(self, until_x, colour="grey", y_range=None):
        """Cover the window from its left edge to ``until_x`` contract units, optionally only rows ``y_range``.

        ``colour`` is "grey", "white", "black" or "noise". The right edge is never covered on site.
        """
        with self._lock:
            self._cover = (until_x, colour, y_range)

    def dock_icon_box(self):
        """Absolute (x, y, w, h) of the Dock icon in latest_frame() coordinates."""
        x, y, w, h = DOCK_ICON_BOX
        window_height = render_log_solution(None, []).shape[0]
        return x, y + window_height, w, h

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def click(self, x, y):
        with self._lock:
            self.events.append(("click", x, y))
            if not ((self._hidden or self._cover) and self.restore_on_dock and self.dock_visible):
                return
            x0, y0, w, h = self.dock_icon_box()
            if not (x0 <= x < x0 + w and y0 <= y < y0 + h):
                return
            self._hidden = False
            self._cover = None
            if self.state_on_dock is not None:
                self._set_state_locked(self.state_on_dock)
            elif self._state is None:
                self._set_state_locked("standby")

    async def move(self, x, y):
        with self._lock:
            self.events.append(("move", x, y))

    async def press_command_shift_m(self):
        with self._lock:
            self.events.append(("key", "cmd+shift+m"))
            if self.shortcut_enabled and not self._hidden and self._state in ("standby", "complete"):
                self._set_state_locked("monitoring")

    def _set_state_locked(self, state):
        self._state = state
        if state in ("standby", "monitoring", "review"):
            self._statuses = ["WAITING"] * 4

    def latest_frame(self):
        import time as _time
        import numpy as np
        with self._lock:
            if not self.repeat_sequence or self._seq == 0:
                self._seq += 1
            if not self._frozen:
                self._pts += 0.033
            window = render_log_solution(None if self._hidden else self._state, self._statuses)
            if self._cover is not None and not self._hidden:
                _paint_cover(window, *self._cover)
            strip = np.full((DOCK_HEIGHT, window.shape[1], 3), DOCK_COLOR, dtype=np.uint8)
            if self.dock_visible:
                x, y, w, h = DOCK_ICON_BOX
                strip[y:y + h, x:x + w] = self._dock_icon
            frame = np.vstack([window, strip])
            return (frame, self._seq, _time.monotonic(), "synthetic-stream", self._pts)


_COVER_COLOURS = {"grey": (128, 128, 128), "white": (255, 255, 255), "black": (0, 0, 0)}


def _paint_cover(window, until_x, colour, y_range, scale=2, origin=(23, 31)):
    """Paint an opaque ATE window over the left part of a render_log_solution() frame."""
    import numpy as np
    ox, oy = origin
    x1 = round(ox + until_x * scale)
    y0, y1 = (0, window.shape[0]) if y_range is None else (round(oy + y_range[0] * scale),
                                                          round(oy + y_range[1] * scale))
    if colour == "noise":
        window[y0:y1, :x1] = np.random.RandomState(7).randint(0, 256, (y1 - y0, x1, 3), dtype=np.uint8)
    else:
        window[y0:y1, :x1] = _COVER_COLOURS[colour]


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
