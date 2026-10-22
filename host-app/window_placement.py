# -*- coding: utf-8 -*-
"""Where the Monitor window opens: inside the usable screen area, top and right edges flush (issue #117).

Windows hides the taskbar from the work area, and the window frame (title bar and borders) takes space
outside the client area. The process is DPI aware (#107), so every number here is in physical pixels,
the same unit Tk uses. Off Windows the whole screen is used and the frame is taken as zero.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, NamedTuple, Tuple

SPI_GETWORKAREA = 0x0030
SM_CXFRAME = 32
SM_CYFRAME = 33
SM_CYCAPTION = 4
SM_CXPADDEDBORDER = 92


class Rect(NamedTuple):
    x: int
    y: int
    width: int
    height: int


class Frame(NamedTuple):
    """Window decoration around the client area, in pixels."""

    left: int
    right: int
    top: int
    bottom: int


NO_FRAME = Frame(0, 0, 0, 0)


def _windows_dll() -> Any:
    """ctypes.windll on Windows; None elsewhere (or when the attribute is missing)."""
    if not sys.platform.startswith("win"):
        return None
    return getattr(ctypes, "windll", None)


def work_area(screen_width: int, screen_height: int, windll: Any = None) -> Rect:
    """Usable screen area: taskbar excluded on Windows (SPI_GETWORKAREA). The whole screen when that is unavailable."""
    whole = Rect(0, 0, screen_width, screen_height)
    if windll is None:
        windll = _windows_dll()
        if windll is None:
            return whole

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = _RECT()
    if not windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return whole
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def frame_size(windll: Any = None) -> Frame:
    """The window frame on Windows (caption, thick border, padded border); zero elsewhere."""
    if windll is None:
        windll = _windows_dll()
        if windll is None:
            return NO_FRAME
    metrics = windll.user32.GetSystemMetrics
    side = metrics(SM_CXFRAME) + metrics(SM_CXPADDEDBORDER)
    bottom = metrics(SM_CYFRAME) + metrics(SM_CXPADDEDBORDER)
    top = metrics(SM_CYCAPTION) + bottom
    return Frame(side, side, top, bottom)


def usable_client_size(area: Rect, frame: Frame) -> Tuple[int, int]:
    """The largest client (width, height) that keeps the whole window, frame included, inside ``area``."""
    return area.width - frame.left - frame.right, area.height - frame.top - frame.bottom


def open_geometry(area: Rect, frame: Frame, screen: Tuple[int, int], content: Tuple[int, int]) -> str:
    """Tk ``geometry`` string for the opening window: ``WxH+X+Y`` with the outer frame flush top-right.

    Width is max(content, 50% of the screen), height is max(content, 85% of the screen), each capped to the
    usable client size, so the window never leaves the work area. ``content`` is the measured minimum.
    """
    screen_w, screen_h = screen
    max_w, max_h = usable_client_size(area, frame)
    width = min(max(content[0], int(screen_w * 0.5)), max_w)
    height = min(max(content[1], int(screen_h * 0.85)), max_h)
    outer_w = width + frame.left + frame.right
    x = area.x + area.width - outer_w
    y = area.y
    return f"{width}x{height}+{x}+{y}"


def placed_area(root: Any) -> Rect:
    """The work area of the screen Tk reports for ``root``."""
    return work_area(root.winfo_screenwidth(), root.winfo_screenheight())
