# -*- coding: utf-8 -*-
"""Exact station-status presentation table for the Monitor UI (issues #120, #126).

Pure-Python module: no Tkinter import. Maps a Core station row to exact display attributes:
the status light, the row colours (spec v5.2 §14.1) and the Online/Offline lamp.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class StationPresentation:
    text_key: str
    light: str
    blink: bool
    row_bg: str = "#F5F5F5"
    row_red: bool = False
    in_banner: bool = False
    status_key: str = "station_offline"
    fg: str = "#616161"
    online: bool = False


_IDLE = StationPresentation("light_idle", "green", False, "#E8F5E9",
                            status_key="station_connected", fg="#2E7D32", online=True)
_BUSY = StationPresentation("light_busy", "yellow", False, "#E3F2FD",
                            status_key="station_busy", fg="#1565C0", online=True)
_FROZEN = StationPresentation("light_frozen", "red", False, "#B71C1C", row_red=True,
                              status_key="station_frozen", fg="#ffffff", online=True)
_DISCONNECTED = StationPresentation("light_disc", "grey", False)
_LOOKUP = {"IDLE": _IDLE, "OK": _IDLE, "BUSY": _BUSY, "TESTING": _BUSY,
           "FROZEN": _FROZEN, "DISCONNECTED": _DISCONNECTED}


def present_station(
    row: Optional[Mapping[str, Any]], core_connected: bool = True
) -> StationPresentation:
    """Frozen overrides device state; a Core outage always displays Offline."""
    if not core_connected:
        return _DISCONNECTED
    row = row or {}
    status = str(row.get("status") or "").strip().upper()
    pres = _FROZEN if row.get("is_frozen") else _LOOKUP.get(status, _DISCONNECTED)
    online = bool(row["is_connected"]) if "is_connected" in row else status in (
        "IDLE", "OK", "BUSY", "TESTING", "FROZEN"
    )
    return replace(pres, online=online)
