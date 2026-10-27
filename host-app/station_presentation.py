# -*- coding: utf-8 -*-
"""Exact station-status presentation table for the Monitor UI (issues #120, #126).

Pure-Python module: no Tkinter import. Maps a Core station row to exact display attributes:
the status light, the row colours (spec v5.2 §14.1) and the Online/Offline lamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    alarm_kind: str = field(default="", compare=False)  # banner category of a red row: "frozen" / "heartbeat" / "heartbeat_not_found" / "connect_failed"
    row_blink: bool = field(default=False, compare=False)  # the whole row flashes (connection failure only, spec v5.2 §4.1)
    status_args: Mapping[str, Any] = field(default_factory=dict, compare=False)  # {n, total} of the Retry words


_IDLE = StationPresentation("light_idle", "green", False, "#E8F5E9",
                            status_key="station_connected", fg="#2E7D32", online=True)
_BUSY = StationPresentation("light_busy", "yellow", False, "#E3F2FD",
                            status_key="station_busy", fg="#1565C0", online=True)
_FROZEN = StationPresentation("light_frozen", "red", False, "#B71C1C", row_red=True,
                              status_key="station_frozen", fg="#ffffff", online=True,
                              alarm_kind="frozen")
_HEARTBEAT = replace(_FROZEN, text_key="light_heartbeat", status_key="station_heartbeat", alarm_kind="heartbeat")
_HEARTBEAT_NOT_FOUND = replace(_FROZEN, text_key="light_heartbeat_not_found", status_key="station_heartbeat_not_found",
                               alarm_kind="heartbeat_not_found")  # spec v5.3 §14.1; issue #130
_CONNECT_FAILED = replace(_FROZEN, text_key="light_connect_failed", status_key="station_connect_failed",
                          alarm_kind="connect_failed", online=False, row_blink=True)  # spec v5.2 §14.1; issue #134
_RETRY = StationPresentation("light_retry", "orange", False, "#FFF3E0", status_key="station_retry_in", fg="#EF6C00")
_DISCONNECTED = StationPresentation("light_disc", "grey", False)
DEFAULT_RETRY_TOTAL = 3
_LOOKUP = {"IDLE": _IDLE, "OK": _IDLE, "BUSY": _BUSY, "TESTING": _BUSY,
           "FROZEN": _FROZEN, "DISCONNECTED": _DISCONNECTED}


def _fail_count(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("connect_fail_count") or 0)
    except (TypeError, ValueError):
        return 0


def _retry_total(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("retry_threshold") or DEFAULT_RETRY_TOTAL)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_TOTAL


def present_station(
    row: Optional[Mapping[str, Any]], core_connected: bool = True
) -> StationPresentation:
    """Frozen wins; connected DISCONNECTED rows use IDLE; Core outages show Offline."""
    if not core_connected:
        return _DISCONNECTED
    row = row or {}
    status = str(row.get("status") or "").strip().upper()
    if status == "DISCONNECTED" and row.get("is_connected"):
        status = "IDLE"
    if row.get("is_frozen"):  # Frame frozen > Heartbeat stopped > Heartbeat not found > the status itself
        pres = _FROZEN
    elif row.get("is_heartbeat_stopped"):
        pres = _HEARTBEAT
    elif row.get("is_heartbeat_not_found"):
        pres = _HEARTBEAT_NOT_FOUND
    elif row.get("is_connected"):
        pres = _LOOKUP.get(status, _DISCONNECTED)  # connected: stale failure fields are ignored
    elif row.get("connect_failed"):
        pres = _CONNECT_FAILED
    elif _fail_count(row) >= 1:
        count = _fail_count(row)
        retrying = bool(row.get("retrying"))
        total = _retry_total(row)
        args = {"n": min(count + 1 if retrying else count, total), "total": total}
        pres = replace(_RETRY, status_key="station_retrying" if retrying else "station_retry_in", status_args=args)
    else:
        pres = _LOOKUP.get(status, _DISCONNECTED)
    online = bool(row["is_connected"]) if "is_connected" in row else status in (
        "IDLE", "OK", "BUSY", "TESTING", "FROZEN"
    )
    return replace(pres, online=online)
