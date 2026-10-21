"""Pin Log Solution cell colours (display contract 1.3, C7) and reject colours off that table.

Hex literals live in this file. They are converted to BGR here and compared with
``STATUS_COLORS``. The test does not rebuild the expected tuple from the constant.
"""

import math
import socket
import sys
from itertools import combinations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from log_solution_reader import COLOR_TOLERANCE, STATUS_COLORS  # noqa: E402
from protocol_helpers import (  # noqa: E402
    LogSolutionKVM, arm_round, make_log_solution_pool, make_registry, recv_reply,
)
from tcp_server import TcpJsonServer  # noqa: E402

# C7 / contract 1.3 STATUS_COLOURS. RGB hex, not BGR.
PINNED_HEX = {
    "PASS": "#00ef00",
    "FAIL": "#ff0000",
    "NOTEST": "#f04bf1",
    "TIMEOUT": "#ff9900",
    "STOPPED": "#bfbfbf",
    "TESTING": "#ffff00",
    "WAITING": "#d9d9d9",
    "COMPLETING": "#82c7ff",
}
PRODUCT_STATUSES = frozenset(("PASS", "FAIL", "NOTEST", "TIMEOUT", "STOPPED"))
OFF_TABLE_HEX = "#8040c0"
OFF_TABLE_SLOT = 2
DEVICE = "DFU-01"


def _bgr(hex_color):
    rgb = int(hex_color.removeprefix("#"), 16)
    red, green, blue = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return (blue, green, red)


def test_status_colors_match_the_pinned_hex_table():
    assert set(STATUS_COLORS) == set(PINNED_HEX)
    for name, hex_color in PINNED_HEX.items():
        assert STATUS_COLORS[name] == _bgr(hex_color)


def test_status_colors_are_farther_apart_than_twice_the_tolerance():
    for left, right in combinations(STATUS_COLORS.values(), 2):
        assert math.dist(left, right) > 2 * COLOR_TOLERANCE


class _OffTableCellKVM(LogSolutionKVM):
    """Same synthetic screen, with one result cell painted a colour outside the table."""

    def latest_frame(self):
        snap = super().latest_frame()
        # Only the finished screen. Painting during Preparing makes the reading
        # unreliable, and monitor_start then looks for the Dock icon.
        if self._state == "complete":
            _paint_slot(snap.image, OFF_TABLE_SLOT, _bgr(OFF_TABLE_HEX))
        return snap


def _paint_slot(image, slot, bgr, scale=2, origin=(23, 31)):
    ox, oy = origin
    row, col = divmod(slot - 1, 10)
    x, y, w, h = col * 34, 34 + row * 27, 34, 26
    x0, y0 = round(ox + x * scale), round(oy + y * scale)
    x1, y1 = round(ox + (x + w) * scale), round(oy + (y + h) * scale)
    image[y0:y1, x0:x1] = bgr


def _send(srv, cmd, req_id):
    host, port = srv.server_address
    payload = {"protocol_version": "1.0", "req_id": req_id, "device": DEVICE,
               "cmd": cmd, "timeout_sec": 0}
    with socket.create_connection((host, port), timeout=10.0) as sock:
        sock.sendall(dumps_request(payload))
        return recv_reply(sock)


@pytest.mark.parametrize("cmd", ["check_start", "check_finish"])
def test_off_table_cell_is_not_taken_and_not_a_product_result(cmd, tmp_path):
    kvm = _OffTableCellKVM(state="standby")
    root = tmp_path / "template"
    root.mkdir()
    srv = TcpJsonServer(
        device_registry=make_registry(),
        host="127.0.0.1",
        port=0,
        kvm_pool=make_log_solution_pool(kvm),
        freeze_guard=FrameFreezeGuard(freeze_threshold=3.0),
        template_root=root,
        monitor_confirm_sec=0.3,
    )
    srv.start()
    try:
        arm_round(srv, kvm, DEVICE)
        kvm.show("complete", ["PASS", "PASS", "PASS", "PASS"])
        # Two polls: a real terminal screen is taken on the confirming frame.
        for index in range(2):
            resp = _send(srv, cmd, f"{cmd}-{index}")
            data = resp["data"]
            assert data["kind"] != "taken"
            assert all(
                row["slot"] != OFF_TABLE_SLOT or row["status"] not in PRODUCT_STATUSES
                for row in data["results"]
            )
    finally:
        srv.stop()
        srv.wait(timeout=2.0)
