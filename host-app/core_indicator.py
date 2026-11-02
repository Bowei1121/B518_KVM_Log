"""Core light of the bridge top bar: what to show for the Core Service, without Tk or sockets.

Six states, each with a colour, a symbol and a text key (the texts live in ui_i18n):

    starting      grey   ○  Core is coming up (bridge just started, or Settings "Restart Core")
    ok            green  √  Core answers; small text "restarted at hh:mm" after an automatic restart
    waiting       orange △  Core stopped, the scheduler brings it back within about one interval
    stopped_long  red    ×  Core has been down longer than the red threshold
    failed        red    ×  Core refused to start: reason from the status file + config path
    invalid_host  red    ×  Core refused to start because its listen address is not this PC

The check interval and the red threshold come from ``windows/core-scheduler.json``, the file the
scheduler install script reads too, so the light and the scheduled task cannot drift apart.
"""

from __future__ import annotations

import datetime
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Seconds a freshly launched Core may take before it counts as stopped (a cold Core.exe start is slow).
START_GRACE_S = 30.0

DEFAULT_SETTINGS = {"interval_minutes": 1, "red_threshold_seconds": 120}

FAIL_TEXT_KEYS = {
    "devices_missing": "core_ind_fail_devices_missing",
    "devices_invalid": "core_ind_fail_devices_invalid",
    "port_in_use": "core_ind_fail_port_in_use",
    "internal_error": "core_ind_fail_internal_error",
}


def scheduler_settings_path() -> Path:
    """core-scheduler.json beside the exe when frozen, otherwise in the repo's windows/ folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "core-scheduler.json"
    return Path(__file__).resolve().parent.parent / "windows" / "core-scheduler.json"


def load_scheduler_settings(path: Optional[Path] = None) -> Dict[str, Any]:
    """Interval (minutes) and red threshold (seconds); defaults when the file is missing or invalid."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(Path(path or scheduler_settings_path()).read_text(encoding="utf-8"))
        for key in settings:
            if isinstance(data.get(key), (int, float)) and data[key] > 0:
                settings[key] = data[key]
    except (OSError, ValueError, AttributeError):
        pass
    return settings


@dataclass(frozen=True)
class Indicator:
    state: str
    light: str  # StatusLight colour key
    symbol: str
    text_key: str
    text_args: Dict[str, Any] = field(default_factory=dict)
    small_key: Optional[str] = None
    small_args: Dict[str, Any] = field(default_factory=dict)


def _parse_time(value: Any) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


class CoreIndicator:
    """Remembers what the bridge saw of Core and turns each new observation into an ``Indicator``."""

    def __init__(self, now: float, wall: datetime.datetime, log_path: str, settings: Optional[Dict[str, Any]] = None,
                 grace_s: float = START_GRACE_S) -> None:
        settings = settings or load_scheduler_settings()
        self.interval_minutes = settings["interval_minutes"]
        self.red_threshold_s = float(settings["red_threshold_seconds"])
        self.grace_s = grace_s
        self.log_path = log_path
        self.launch_at = now  # the bridge started, or last launched / restarted Core
        self.launch_wall = wall
        self.ever_connected = False
        self.last_ok_at = 0.0
        self.last_uptime: Optional[float] = None
        self.was_down = False
        self.restarted_at: Optional[str] = None

    def mark_launch(self, now: float, wall: datetime.datetime) -> None:
        """The bridge started Core (fallback or Settings "Restart Core"): back to "starting"."""
        self.launch_at = now
        self.launch_wall = wall

    def observe(self, ping_ok: bool, uptime: Optional[float], now: float, wall: datetime.datetime,
                status_file: Optional[Dict[str, Any]] = None) -> Indicator:
        """``uptime`` is Core's running seconds; ``status_file`` the parsed core_status.json (ping failed only)."""
        if ping_ok:
            if self.ever_connected and (self.was_down or (
                    uptime is not None and self.last_uptime is not None and uptime < self.last_uptime)):
                self.restarted_at = wall.strftime("%H:%M")
            self.ever_connected = True
            self.was_down = False
            self.last_ok_at = now
            self.last_uptime = uptime
            small = ("core_ind_restarted", {"time": self.restarted_at}) if self.restarted_at else (None, {})
            return Indicator("ok", "green", "√", "core_ind_ok", small_key=small[0], small_args=small[1])

        failed = self._failure(status_file)
        if failed:
            reason, detail, devices_file = failed
            if reason == "invalid_host":
                return Indicator("invalid_host", "red", "×", "core_ind_invalid_host",
                                 small_key="core_ind_invalid_host_small", small_args={"path": self.log_path})
            key = FAIL_TEXT_KEYS.get(reason, FAIL_TEXT_KEYS["internal_error"])
            path = devices_file if reason in ("devices_missing", "devices_invalid") else self.log_path
            return Indicator("failed", "red", "×", key, {"detail": detail, "port": status_file.get("port")},
                             "core_ind_check_config", {"path": path})
        if now - self.launch_at < self.grace_s:
            return Indicator("starting", "grey", "○", "core_ind_starting")
        self.was_down = self.ever_connected  # past the start grace: an ok after this means Core was restarted
        down_for = now - max(self.launch_at, self.last_ok_at)
        minutes = math.ceil(self.red_threshold_s / 60)
        if down_for > self.red_threshold_s:
            return Indicator("stopped_long", "red", "×", "core_ind_stopped_long", {"minutes": minutes},
                             "core_ind_path", {"path": self.log_path})
        return Indicator("waiting", "orange", "△", "core_ind_waiting", {"minutes": self.interval_minutes})

    def _failure(self, status_file: Optional[Dict[str, Any]]):
        """(reason, detail, devices_file) when the status file records a failed start after this launch."""
        if not status_file or status_file.get("state") != "failed":
            return None
        stamp = _parse_time(status_file.get("updated_at"))
        if stamp is None:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()
        if stamp < self.launch_wall.astimezone():
            return None
        return status_file.get("reason"), status_file.get("detail", ""), status_file.get("devices_file", "")
