# -*- coding: utf-8 -*-
"""Exact station-status presentation table for the Monitor UI (issue #120).

Pure-Python module: no Tkinter import. Maps Core station status strings to exact display attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# Same value as ui_widgets.WINDOW_BG (kept Tk-free here; a test pins the two together).
WINDOW_BG = "#f0f0f0"


@dataclass(frozen=True)
class StationPresentation:
    text_key: str
    light: str
    blink: bool
    row_bg: str = WINDOW_BG
    row_red: bool = False
    in_banner: bool = False


_PRESENTATION_IDLE = StationPresentation(
    text_key="light_idle",
    light="green",
    blink=False,
    row_bg=WINDOW_BG,
    row_red=False,
    in_banner=False,
)

_PRESENTATION_BUSY = StationPresentation(
    text_key="light_busy",
    light="yellow",
    blink=False,
    row_bg=WINDOW_BG,
    row_red=False,
    in_banner=False,
)

_PRESENTATION_FROZEN = StationPresentation(
    text_key="light_frozen",
    light="red",
    blink=True,
    row_bg=WINDOW_BG,
    row_red=False,
    in_banner=False,
)

_PRESENTATION_DISCONNECTED = StationPresentation(
    text_key="light_disc",
    light="red",
    blink=False,
    row_bg=WINDOW_BG,
    row_red=False,
    in_banner=False,
)

_EXACT_LOOKUP: Dict[str, StationPresentation] = {
    "IDLE": _PRESENTATION_IDLE,
    "OK": _PRESENTATION_IDLE,
    "BUSY": _PRESENTATION_BUSY,
    "TESTING": _PRESENTATION_BUSY,
    "FROZEN": _PRESENTATION_FROZEN,
    "DISCONNECTED": _PRESENTATION_DISCONNECTED,
}


def present_station(
    row: Optional[Mapping[str, Any]], core_connected: bool = True
) -> StationPresentation:
    """Return the exact display presentation for a Core device row.

    Normalises the status with str(status or "").strip().upper(). If core_connected is False,
    returns the DISCONNECTED presentation regardless of status. Unknown or empty statuses
    also fall back to DISCONNECTED.
    """
    if not core_connected:
        return _PRESENTATION_DISCONNECTED
    status = row.get("status") if row is not None else None
    norm = str(status or "").strip().upper()
    return _EXACT_LOOKUP.get(norm, _PRESENTATION_DISCONNECTED)
