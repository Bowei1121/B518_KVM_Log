"""Synthetic DFU window with real template matching, for DFU input tests.

The screen is drawn from the same images the templates are saved from, so matching
is exact. Clicks on a checkbox toggle it, and every HID call is recorded.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from template_catalog import TemplateCatalog  # noqa: E402

BOX = 14
WINDOW = (10, 10, 240, 190)  # x, y, w, h on the screen
DOCK = (5, 215, 40, 20)
INPUT = (30, 150, 80, 16)
BUTTON = (150, 175, 50, 18)
BACKGROUND = (200, 200, 200)


def _noise(seed, shape):
    return np.random.RandomState(seed).randint(0, 255, shape + (3,), dtype=np.uint8)


def _draw_checkbox(canvas, x, y, checked):
    canvas[y:y + BOX, x:x + BOX] = (255, 255, 255)
    canvas[y:y + 2, x:x + BOX] = 0
    canvas[y + BOX - 2:y + BOX, x:x + BOX] = 0
    canvas[y:y + BOX, x:x + 2] = 0
    canvas[y:y + BOX, x + BOX - 2:x + BOX] = 0
    if checked:
        for i in range(3, BOX - 3):
            canvas[y + i, x + i] = (0, 0, 255)
            canvas[y + i, x + BOX - 1 - i] = (0, 0, 255)


def checkbox_templates():
    checked = np.full((BOX, BOX, 3), 200, dtype=np.uint8)
    unchecked = np.full((BOX, BOX, 3), 200, dtype=np.uint8)
    _draw_checkbox(checked, 0, 0, True)
    _draw_checkbox(unchecked, 0, 0, False)
    return checked, unchecked


class SyntheticDfuKVM:
    """A DFU window with checkboxes at ``positions`` (screen x, y), in the given initial states."""

    def __init__(self, positions, checked=None, log_state="standby", log_statuses=None):
        self.positions = list(positions)
        self.state = [False] * len(self.positions) if checked is None else list(checked)
        self.events = []
        self.rpc_errors = []
        self.log_state = log_state
        self.log_statuses = log_statuses
        self.window_patch = _noise(1, (WINDOW[3], WINDOW[2]))
        self.dock_patch = _noise(2, (DOCK[3], DOCK[2]))
        self.input_patch = _noise(3, (INPUT[3], INPUT[2]))
        self.button_patch = _noise(4, (BUTTON[3], BUTTON[2]))
        self.hid_error_after_types = None  # type-count after which a HID error is reported
        self.lost_clicks = []  # one entry per click to ignore, by checkbox index
        self.frame = self._render()

    def _render(self):
        canvas = np.full((240, 260, 3), 230, dtype=np.uint8)
        x, y, w, h = WINDOW
        canvas[y:y + h, x:x + w] = BACKGROUND
        for index, (px, py) in enumerate(self.positions):
            _draw_checkbox(canvas, px, py, self.state[index])
        dx, dy, dw, dh = DOCK
        canvas[dy:dy + dh, dx:dx + dw] = self.dock_patch
        ix, iy, iw, ih = INPUT
        canvas[iy:iy + ih, ix:ix + iw] = self.input_patch
        bx, by, bw, bh = BUTTON
        canvas[by:by + bh, bx:bx + bw] = self.button_patch
        return canvas

    async def click(self, x, y):
        self.events.append(("click", x, y))
        for index, (px, py) in enumerate(self.positions):
            if px <= x < px + BOX and py <= y < py + BOX:
                if index in self.lost_clicks:
                    self.lost_clicks.remove(index)
                    return
                self.state[index] = not self.state[index]
                self.frame = self._render()
                return

    async def type_text(self, text):
        self.events.append(("type", text))
        typed = sum(1 for event in self.events if event[0] == "type")
        if self.hid_error_after_types is not None and typed > self.hid_error_after_types:
            self.rpc_errors.append("keyboard lost")

    async def press_enter(self):
        self.events.append(("enter",))

    async def press_command_shift_m(self):
        self.events.append(("log_shortcut",))

    def latest_frame(self):
        import time as _time
        from protocol_helpers import render_log_solution

        frame = render_log_solution(self.log_state, self.log_statuses or ["WAITING"] * 4)
        return (frame, len(self.events) + 1, _time.monotonic(), "synthetic", _time.monotonic())

    def checked_channels(self):
        """1-based channel numbers in order, for checkboxes currently checked."""
        order = sorted(range(len(self.positions)), key=lambda i: (self.positions[i][1], self.positions[i][0]))
        return [channel for channel, index in enumerate(order, 1) if self.state[index]]


def save_templates(root, screen_positions, initial_checked=None):
    """Save dock, window, checkbox, input and button templates cut from a DFU screen."""
    catalog = TemplateCatalog(root)
    kvm = SyntheticDfuKVM(screen_positions, initial_checked)
    checked, unchecked = checkbox_templates()
    x, y, w, h = WINDOW
    window_image = kvm.frame[y:y + h, x:x + w].copy()
    dx, dy, dw, dh = DOCK
    catalog.save_crop(kvm.frame[dy:dy + dh, dx:dx + dw].copy(), (0, 0, dw, dh), "DFU", "dock_icon")
    catalog.save_crop(window_image, (0, 0, w, h), "DFU", "window")
    catalog.save_crop(checked, (0, 0, BOX, BOX), "DFU", "checkbox_checked")
    catalog.save_crop(unchecked, (0, 0, BOX, BOX), "DFU", "checkbox_unchecked")
    ix, iy, iw, ih = INPUT
    catalog.save_crop(kvm.frame[iy:iy + ih, ix:ix + iw].copy(), (0, 0, iw, ih), "DFU", "input")
    bx, by, bw, bh = BUTTON
    catalog.save_crop(kvm.frame[by:by + bh, bx:bx + bw].copy(), (0, 0, bw, bh), "DFU", "button")
    return catalog
