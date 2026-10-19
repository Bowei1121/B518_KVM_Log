"""Headless TCP JSON Protocol & Multiplexing Server.

Provides a standalone, headless TCP server that communicates with LabVIEW and
other clients using newline-delimited JSON. Supports concurrent multiplexed
client connections, req_id correlation, and graceful shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import logging
from pathlib import Path
import signal
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Coroutine, Dict, Iterable, Optional, Set, Tuple

try:
    from device_registry import DeviceRegistryError
except ImportError:
    from .device_registry import DeviceRegistryError  # type: ignore[import-not-found,no-redef]

try:
    from device_lock import DeviceLockManager
except ImportError:
    from .device_lock import DeviceLockManager  # type: ignore[import-not-found,no-redef]

try:
    from kvm_pool import KVMConnectionPool
except ImportError:
    from .kvm_pool import KVMConnectionPool  # type: ignore[import-not-found,no-redef]

try:
    from freeze_guard import FrameFreezeGuard
except ImportError:
    from .freeze_guard import FrameFreezeGuard  # type: ignore[import-not-found,no-redef]

try:
    from device_registry import DeviceRegistry
except ImportError:
    from .device_registry import DeviceRegistry  # type: ignore[import-not-found,no-redef]

try:
    from monitor_flow import MONITOR_CONFIRM_SEC
except ImportError:
    from .monitor_flow import MONITOR_CONFIRM_SEC  # type: ignore[import-not-found,no-redef]

MONITOR_START_MAX_TIMEOUT_SEC = 300

try:
    from bt_flow import BtStartError, run_start_all
    from dfu_flow import DfuError, parse_items, run_dfu_input, run_dfu_ok
except ImportError:
    from .bt_flow import BtStartError, run_start_all  # type: ignore[import-not-found,no-redef]
    from .dfu_flow import DfuError, parse_items, run_dfu_input, run_dfu_ok  # type: ignore[import-not-found,no-redef]

logger = logging.getLogger("TcpJsonServer")

try:
    from core_protocol import COMMON_FIELDS, ENVELOPE_KEYS, PROTOCOL_VERSION
except ImportError:
    from .core_protocol import COMMON_FIELDS, ENVELOPE_KEYS, PROTOCOL_VERSION  # type: ignore[import-not-found,no-redef]

CORE_VERSION = "2.0.0"
CHECK_MAX_TIMEOUT_SEC = 3600
CHECK_POLL_INTERVAL_SEC = 0.5
INSPECTION_TIMEOUT_SEC = 0.2  # per-frame limit for one Log Solution reading
VISIBILITY_LOST_SEC = 30.0  # time "Log Solution not visible" (no reliable reading) before a Dock click
DOCK_RECHECK_SEC = 5.0  # wait after the Dock click before reading again
MAX_DOCK_RECOVERIES = 3  # Dock clicks per check request
# Picture/stream problems: a Dock click cannot fix them, so they neither count as "Log Solution not
# visible" nor reset the visibility timer. Every other unknown reading counts as not visible
# (wholly or partly covered, closed, minimised), including reasons added later.
STREAM_PROBLEM_REASONS = frozenset({
    "no_frame", "invalid_frame", "stale_frame", "missing_source_frame_time", "out_of_order_source_frame",
    "duplicate_or_out_of_order_frame", "inspection_busy", "inspection_timeout",
})


def _visibility(obs: Dict[str, Any]) -> str:
    """'ignore' for a stream problem, 'unseen' for any other unknown reading, 'seen' for a reliable one."""
    if obs.get("reason") in STREAM_PROBLEM_REASONS:
        return "ignore"
    return "unseen" if obs.get("kind") == "unknown" else "seen"


NO_DEVICE_COMMANDS = frozenset({"ping", "status", "core_status", "shutdown"})
# Extra fields allowed per command.
COMMAND_FIELDS: Dict[str, frozenset] = {
    "ping": frozenset(),
    "status": frozenset(),
    "core_status": frozenset(),
    "shutdown": frozenset(),
    "snapshot": frozenset({"format", "quality"}),
    "check_finish": frozenset({"timeout_sec"}),
    "check_start": frozenset({"timeout_sec"}),
}
ERROR_CODE_MAP = {
    "kvm_connection_failed": "kvm_connect_failed",
    "missing_kvm": "kvm_connect_failed",
}
# Station-limited commands: the operation each station runs. A station without an entry is refused
# with unsupported_command during request validation, before any HID.
STATION_OPERATIONS: Dict[str, Dict[str, Callable[..., Any]]] = {
    "input": {"DFU": run_dfu_input},
    "start_test": {"DFU": run_dfu_ok, "BT": run_start_all},
}
UNSUPPORTED_STATION_MESSAGES = {
    "input": "'input' is only supported for DFU devices",
    "start_test": "'start_test' is not supported for FCT; FCT starts when the fixture is pushed in",
}
DEFAULT_DATA: Dict[str, Dict[str, Any]] = {
    "ping": {"pong": False, "core_version": ""},
    "status": {"core_uptime_sec": 0, "devices": []},
    "snapshot": {"width": 0, "height": 0, "format": "", "image_base64": ""},
    "check_finish": {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []},
    "check_start": {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []},
    "input": {"entered": []},
    "start_test": {"action": ""},
    "monitor_start": {"state": "", "capacity": 0},
    "inspect": {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []},
}


def _unknown(reason: str) -> Dict[str, Any]:
    return {"kind": "unknown", "state": "", "reason": reason, "capacity": 0, "active": [], "results": []}


def _check_error(code: str, message: str, last: Dict[str, Any]) -> Dict[str, Any]:
    """check/inspect error reply: data keeps the last observation, with kind unknown and no results."""
    return {"status": "error", "error": code, "message": message,
            "data": {**last, "kind": "unknown", "results": []}}


def _round_observation(decision: Any) -> Dict[str, Any]:
    """Map a round-gate decision onto the check 1.0 data shape."""
    if decision.kind == "frozen" or decision.reason == "frame_frozen":
        return {"error": "frame_frozen", "message": "Log Solution frame stream is frozen"}
    if decision.kind == "round_lost":
        return {"error": "round_lost", "message": "Log Solution returned to standby before this round was taken"}
    if decision.reason == "no_frame":
        return {"error": "no_frame", "message": "No frame received from KVM yet"}
    return {
        "kind": decision.kind,
        "state": decision.state.value if decision.state is not None else "",
        "reason": decision.reason,
        "capacity": int(decision.capacity),
        "active": [],
        "results": [{"slot": slot, "status": status} for slot, status in decision.results],
    }


def _inspect_round(kvm: Any, tracker: Any) -> Tuple[Dict[str, Any], Any]:
    """Read-only reading of the Log Solution screen. Runs on a worker thread.

    Returns ``(reply, state)``. Reports what the screen shows now. Never returns taken and never
    arms or consumes the device's round gate. Two agreeing fresh frames fill ``active``.
    """
    from log_solution_reader import (
        CONFIRMATION_REQUIRED, ScreenKind, read_app_frame, reportable_active, screen_kind,
    )

    snapshot, obs = read_app_frame(kvm)
    if snapshot is None:
        return {"error": "no_frame", "message": "No frame received from KVM yet"}, None
    rows = tracker.observe(obs, snapshot.sequence, snapshot.received_monotonic)
    kind = screen_kind(obs)
    if kind is ScreenKind.UNKNOWN:
        return _unknown(obs.reason), None
    state = obs.state
    # Key order matches the 1.0 reply on main: state, capacity, active, kind, reason, results.
    # Finished screens report no started channels (reportable_active), so active stays [] there.
    active = [{"slot": slot, "evidence": evidence} for slot, evidence in reportable_active(state, rows)]
    base = {"state": state.value, "capacity": int(obs.capacity), "active": active}
    if kind is ScreenKind.PAUSED:
        return {**base, "kind": "paused", "reason": CONFIRMATION_REQUIRED, "results": [], "active": []}, state
    return {**base, "kind": "waiting", "reason": state.value, "results": []}, state


class TcpJsonServer:
    """Headless TCP Server using newline-delimited JSON protocol."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        lock_manager: Optional[DeviceLockManager] = None,
        kvm_pool: Optional[KVMConnectionPool] = None,
        freeze_guard: Optional[FrameFreezeGuard] = None,
        device_registry: Optional[DeviceRegistry] = None,
        template_root: Optional[str | Path] = None,
        inspection_timeout_sec: float = INSPECTION_TIMEOUT_SEC,
        monitor_confirm_sec: float = MONITOR_CONFIRM_SEC,
        visibility_lost_sec: float = VISIBILITY_LOST_SEC,
        dock_recheck_sec: float = DOCK_RECHECK_SEC,
        max_dock_recoveries: int = MAX_DOCK_RECOVERIES,
    ) -> None:
        self.host = host
        # None → TemplateCatalog.default_root() at check time (~/Documents/template)
        self.template_root = template_root
        self.port = port
        self.server_address: tuple[str, int] = (host, port)
        self.lock_manager = lock_manager if lock_manager is not None else DeviceLockManager()
        self.kvm_pool = kvm_pool if kvm_pool is not None else KVMConnectionPool()
        self.freeze_guard = freeze_guard if freeze_guard is not None else FrameFreezeGuard()
        self.device_registry: DeviceRegistry = (
            device_registry if device_registry is not None else DeviceRegistry([])
        )

        self._server: Optional[asyncio.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_event = threading.Event()
        self._shutdown_event: Optional[asyncio.Event] = None

        self._active_writers: Set[asyncio.StreamWriter] = set()
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._round_gates: Dict[str, Any] = {}
        self._rounds: Dict[str, RoundTrack] = {}
        self._inspect_activity: Dict[str, Any] = {}
        self.inspection_timeout_sec = float(inspection_timeout_sec)
        self.monitor_confirm_sec = float(monitor_confirm_sec)
        self.visibility_lost_sec = float(visibility_lost_sec)
        self.dock_recheck_sec = float(dock_recheck_sec)
        self.max_dock_recoveries = int(max_dock_recoveries)
        # Frame readings run on worker threads so the event loop keeps serving requests.
        self._inspector = ThreadPoolExecutor(max_workers=16, thread_name_prefix="inspect")
        self._inspections: Dict[str, Future] = {}
        self._command_fields: Dict[str, frozenset] = dict(COMMAND_FIELDS)
        self._no_device_cmds: Set[str] = set(NO_DEVICE_COMMANDS)

        self._start_time: float = 0.0
        self._is_running = False

        self._register_default_handlers()

    @property
    def is_running(self) -> bool:
        """Return True if the server is currently running."""
        return self._is_running

    def register_handler(
        self,
        cmd: str,
        handler: Callable[[Dict[str, Any]], Any],
        fields: Optional[Iterable[str]] = None,
        device_bound: Optional[bool] = None,
    ) -> None:
        """Register a command handler.

        Handler can be either a synchronous callable or an async coroutine.
        It accepts the request dict and returns the data/response dict.
        ``fields`` lists the extra request fields the command accepts; ``device_bound``
        makes the command require a registered device ID.
        """
        self._handlers[cmd] = handler
        if fields is not None:
            self._command_fields[cmd] = frozenset(fields)
        else:
            self._command_fields.setdefault(cmd, frozenset())
        if device_bound is None:
            device_bound = cmd in COMMAND_FIELDS and cmd not in NO_DEVICE_COMMANDS
        if device_bound:
            self._no_device_cmds.discard(cmd)
        else:
            self._no_device_cmds.add(cmd)

    def _register_default_handlers(self) -> None:
        self.register_handler("ping", self._handle_ping)
        self.register_handler("status", self._handle_status)
        self.register_handler("core_status", self._handle_core_status)
        self.register_handler("shutdown", self._handle_shutdown)
        self.register_handler("check_finish", self._handle_check_finish, device_bound=True)
        self.register_handler("check_start", self._handle_check_start, device_bound=True)
        self.register_handler("inspect", self._handle_inspect, fields=("timeout_sec",), device_bound=True)
        self.register_handler("input", self._handle_input, fields=("items",), device_bound=True)
        self.register_handler("start_test", self._handle_start_test, device_bound=True)
        self.register_handler("monitor_start", self._handle_monitor_start, fields=("timeout_sec",),
                              device_bound=True)
        self.register_handler("snapshot", self._handle_snapshot)

    async def _handle_ping(self, req: Dict[str, Any]) -> Dict[str, Any]:
        return {"pong": True, "core_version": CORE_VERSION}

    async def _handle_stations(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Return real-time status of all configured test stations."""
        active_devices = set(self.lock_manager.get_active_devices())
        fg_status = self.freeze_guard.get_status()
        frozen_devices = set(fg_status.get("frozen_devices", []))
        active_ips = set(self.kvm_pool.get_active_ips())

        stations_list: list[Dict[str, Any]] = []
        for entry in self.device_registry.entries():
            st_name = entry.station
            dev_id = entry.device_id
            kvm_ip = entry.kvm_ip

            is_busy = dev_id in active_devices
            is_frozen = dev_id in frozen_devices
            is_connected = kvm_ip in active_ips

            if is_busy:
                status_str = "BUSY"
            elif is_frozen:
                status_str = "FROZEN"
            elif is_connected:
                status_str = "IDLE"
            else:
                status_str = "DISCONNECTED"

            stations_list.append({
                "station": st_name,
                "device": dev_id,
                "kvm_ip": kvm_ip,
                "status": status_str,
                "is_busy": is_busy,
                "is_frozen": is_frozen,
                "is_connected": is_connected,
            })

        return {
            "status": "ok",
            "stations": stations_list,
        }

    async def _handle_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """LabVIEW status: Core uptime and per-device state. KVM IPs are never returned."""
        uptime = time.time() - self._start_time if self._start_time else 0.0
        active_devices = set(self.lock_manager.get_active_devices())
        frozen_devices = set(self.freeze_guard.get_status().get("frozen_devices", []))
        active_ips = set(self.kvm_pool.get_active_ips())
        devices: list[Dict[str, Any]] = []
        for entry in self.device_registry.entries():
            devices.append({
                "device": entry.device_id,
                "station": entry.station,
                "connected": entry.kvm_ip in active_ips,
                "busy": entry.device_id in active_devices,
                "frozen": entry.device_id in frozen_devices,
                "channels": entry.channels,
            })
        return {"core_uptime_sec": int(uptime), "devices": devices}

    async def _handle_core_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Full internal Core state for Monitor UI / admin tools (not part of the LabVIEW protocol)."""
        uptime = time.time() - self._start_time if self._start_time else 0.0
        active_devices = self.lock_manager.get_active_devices()
        stations_res = await self._handle_stations(req)
        return {
            "status": "running",
            "uptime_seconds": round(uptime, 2),
            "active_connections": len(self._active_writers),
            "busy_devices": active_devices,
            "kvm_pool": {
                "active_connections": self.kvm_pool.active_count(),
                "cached_ips": self.kvm_pool.get_active_ips(),
            },
            "freeze_guard": self.freeze_guard.get_status(),
            "stations": stations_res.get("stations", []),
            "version": "1.0.0",
        }

    async def _handle_snapshot(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Request and return a single frame snapshot as base64 without acquiring device locks."""
        kvm = req.get("_kvm_client")
        device_id = str(req.get("device") or "")

        if kvm is None:
            return {
                "status": "error",
                "error": "missing_kvm",
                "message": "KVM connection not available for this device",
                "device": device_id,
            }

        latest = kvm.latest_frame()
        if latest is None:
            return {
                "status": "error",
                "error": "no_frame",
                "message": "No frame received from KVM yet",
                "device": device_id,
            }
        frame, pts = latest.image, latest.pts

        # Encode to JPEG/PNG base64
        fmt = req["format"]
        quality = req["quality"]
        img_b64 = ""
        width = 0
        height = 0

        try:
            import cv2
            if hasattr(frame, "shape"):
                height, width = frame.shape[:2]
                ext = ".png" if fmt == "png" else ".jpg"
                params = [int(cv2.IMWRITE_JPEG_QUALITY), quality] if fmt == "jpeg" else []
                success, buf = cv2.imencode(ext, frame, params)
                if success:
                    img_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            elif isinstance(frame, (bytes, bytearray)):
                img_b64 = base64.b64encode(frame).decode("ascii")
            elif isinstance(frame, str):
                img_b64 = base64.b64encode(frame.encode("utf-8")).decode("ascii")
        except Exception as exc:
            logger.warning("Failed to encode frame to base64: %s", exc)

        return {
            "status": "ok",
            "width": width,
            "height": height,
            "format": fmt,
            "image_base64": img_b64,
        }

    async def _observe_check(self, kvm: Any, entry: Any, req: Dict[str, Any], read_only: bool = False,
                             report_activity: bool = False) -> Tuple[Dict[str, Any], Any]:
        """One reading of the Log Solution screen for a device, as ``(1.0 check payload, MarkerState)``.

        The reading runs on a worker thread with a per-frame time limit. A reading that
        exceeds the limit is reported as unknown and the poll continues; a device whose
        previous reading is still running is reported as unknown without starting another.
        """
        flight_key = f"inspect:{entry.device_id}" if read_only else entry.device_id
        previous = self._inspections.get(flight_key)
        if previous is not None and not previous.done():
            return _unknown("inspection_busy"), None

        # Every station reads the Log Solution display contract; the station only affects operations.
        from round_frame_consumer import observe_latest_round_frame
        gate = None
        if read_only:
            # Gate-less reading: never arms, takes or otherwise touches the round.
            tracker = self._inspect_tracker(entry.device_id)

            def job():
                return _inspect_round(kvm, tracker)
        else:
            gate = self._round_gate(entry.device_id)
            accept_round = self._round_track(entry.device_id).start_requested

            def job():
                return observe_latest_round_frame(kvm, gate, accept_round=accept_round)

        future = self._inspector.submit(job)
        self._inspections[flight_key] = future
        try:
            raw = await asyncio.wait_for(asyncio.wrap_future(future), timeout=self.inspection_timeout_sec)
        except asyncio.TimeoutError:
            logger.debug("Inspection for %s exceeded %.3f s; reported as unknown", entry.device_id,
                         self.inspection_timeout_sec)
            return _unknown("inspection_timeout"), None
        except Exception as exc:
            logger.exception("Inspection error for %s", entry.device_id)
            return {"error": "internal_error", "message": f"inspection error: {exc}"}, None

        if read_only:
            return raw
        from log_solution_reader import reportable_active

        mapped = _round_observation(raw)
        # taken / already_taken / paused keep active []. Activity is only for in-progress rounds.
        if report_activity and gate is not None and "error" not in mapped and mapped.get("kind") == "waiting":
            mapped["active"] = [{"slot": slot, "evidence": evidence}
                                for slot, evidence in reportable_active(raw.state, gate.last_active)]
        return mapped, raw.state

    def _round_track(self, device_id: str) -> "RoundTrack":
        """Per-device round-start request. Lives as long as the server; a timeout does not clear it."""
        track = self._rounds.get(device_id)
        if track is None:
            track = RoundTrack()
            self._rounds[device_id] = track
        return track

    def _inspect_tracker(self, device_id: str) -> Any:
        """Started-channel tracker of the Monitor UI inspect. Its own instance: check_start/check_finish never share it."""
        from log_solution_reader import ActivityTracker

        tracker = self._inspect_activity.get(device_id)
        if tracker is None:
            tracker = ActivityTracker(max_gap=1.0)
            self._inspect_activity[device_id] = tracker
        return tracker

    def _account_round_screen(self, device_id: str, state: Any) -> None:
        """Preparing or monitoring is a new-round signal. Monitoring counts only while a request exists."""
        from log_solution_reader import MarkerState

        track = self._round_track(device_id)
        if state == MarkerState.PREPARING:
            track.monitoring_seen = False
            track.input_released = False
        elif state == MarkerState.MONITORING:
            track.input_released = False
            if track.start_requested:
                track.monitoring_seen = True

    def _round_start_refusal(self, device_id: str, obs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """check_start / check_finish steps (4) and (5). None when judgment should continue.

        (1) paused, (2) already_taken and (3) unknown are handled by the caller before this.
        """
        track = self._round_track(device_id)
        if not track.start_requested:
            return _check_error("monitor_not_ready",
                                "no round-start request; result was not taken", obs)
        # Gate not armed (or this call must not take) while a deliverable screen is up: never hang.
        # Busy-on-monitoring must not invent monitoring_seen; capacity changes also disarm the gate.
        if obs.get("reason") == "deliverable_without_monitoring":
            track.settle()
            return _check_error(
                "manual_verification_required",
                "start was requested but this round's monitoring was not seen", obs)
        return None

    def _round_gate(self, device_id: str) -> Any:
        """The device's round gate, created on first use; it lives as long as the server."""
        from round_frame_consumer import RoundFrameGate

        gate = self._round_gates.get(device_id)
        if gate is None:
            gate = RoundFrameGate(device_id, require_presentation_time=True)
            self._round_gates[device_id] = gate
        return gate

    def _template_root(self) -> Any:
        """Configured template root, or TemplateCatalog.default_root() (~/Documents/template)."""
        from template_catalog import TemplateCatalog

        return self.template_root or TemplateCatalog.default_root()

    def _log_solution_refusal(self, kvm: Any, entry: Any) -> Optional[Tuple[str, str]]:
        """Why SN input must wait, as (code, message): None when the Log Solution is in standby or its round was taken."""
        from log_solution_reader import MarkerState, read_latest_app_frame

        frame, observation = read_latest_app_frame(kvm)
        if frame is None:
            return "hid_failed", "no frame to read the Log Solution state"
        if not observation.reliable:
            return "hid_failed", "Log Solution state unreadable"
        if observation.state == MarkerState.STANDBY:
            return None
        # complete and manual_stop_complete are both READY. Input waits until this round
        # was delivered or manual_verification_required was already returned.
        if observation.finished:
            if self._round_track(entry.device_id).input_released:
                return None
            return "log_solution_busy", "round result not yet taken"
        return "log_solution_busy", f"Log Solution state is {observation.state.value}"

    def _monitor_refusal(self, kvm: Any) -> Optional[Tuple[str, str]]:
        """Why start_test must wait, as (code, message): None when a reliable Log Solution frame reads Monitoring."""
        from log_solution_reader import MarkerState, read_latest_app_frame

        frame, observation = read_latest_app_frame(kvm)
        if frame is None:
            return "monitor_not_ready", "Log Solution is not monitoring (no frame); no HID sent"
        if observation.reliable and observation.state == MarkerState.MONITORING:
            return None
        state = observation.state.value if observation.reliable else "unreadable"
        return "monitor_not_ready", f"Log Solution is not monitoring ({state}); no HID sent"

    async def _handle_start_test(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """start_test 1.0: DFU clicks OK, BT clicks Start All. Both only while Log Solution is monitoring."""
        kvm = req["_kvm_client"]
        entry = req["_device_entry"]
        refusal = await asyncio.wrap_future(self._inspector.submit(self._monitor_refusal, kvm))
        if refusal is not None:
            code, message = refusal
            return {"status": "error", "error": code, "message": message, "data": {"action": ""}}

        start = STATION_OPERATIONS["start_test"][entry.station]
        try:
            result = await start(kvm, template_root=self._template_root(),
                                 log=logger.info, diagnostics_key=entry.device_id)
        except (BtStartError, DfuError) as exc:
            return {"status": "error", "error": exc.code, "message": str(exc), "data": {"action": ""}}
        return {"status": "ok", "data": result}

    async def _handle_monitor_start(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """monitor_start 1.0: open Log Solution from the Dock and start monitoring (all stations)."""
        from monitor_flow import MonitorStartError, run_monitor_start

        entry = req["_device_entry"]
        track = self._round_track(entry.device_id)
        track.begin_start()
        self._round_gate(entry.device_id).forget_delivered_round()
        template_root = self._template_root()
        try:
            result = await run_monitor_start(
                req["_kvm_client"], self._round_gate(entry.device_id), template_root=template_root,
                confirm_sec=self.monitor_confirm_sec, start_timeout_sec=float(req["timeout_sec"]),
                log=logger.info, diagnostics_key=entry.device_id,
                on_preparing=track.note_start, on_shortcut=track.note_start,
                on_monitoring=track.note_monitoring)
        except MonitorStartError as exc:
            # monitor_not_started keeps the round-start request. Only a later taken or
            # manual_verification_required reply clears it.
            return {"status": "error", "error": exc.code, "message": str(exc), "data": {"state": "", "capacity": 0}}
        return {"status": "ok", "data": result}

    async def _handle_input(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """DFU input 1.0: refuse while a round runs, otherwise enter SNs by channel."""
        kvm = req.get("_kvm_client")
        entry = req["_device_entry"]
        refusal = await asyncio.wrap_future(self._inspector.submit(self._log_solution_refusal, kvm, entry))
        if refusal is not None:
            code, message = refusal
            return {"status": "error", "error": code, "message": message, "data": {"entered": []}}

        enter = STATION_OPERATIONS["input"][entry.station]
        try:
            result = await enter(kvm, entry.channels, req.get("items"), template_root=self._template_root(),
                                 log=logger.info, diagnostics_key=entry.device_id)
        except DfuError as exc:
            return {"status": "error", "error": exc.code, "message": str(exc), "data": {"entered": exc.entered}}
        return {"status": "ok", "data": result}

    async def _handle_check_start(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """check_start: two fresh frames of activity, or a finished round delivered once."""
        return await self._long_poll(req, read_only=False, report_activity=True)

    async def _handle_check_finish(self, req: Dict[str, Any]) -> Dict[str, Any]:
        return await self._long_poll(req, read_only=False)

    async def _handle_inspect(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor UI's read-only check: no device lock, and it never consumes the round result."""
        return await self._long_poll(req, read_only=True, report_activity=True)

    async def _long_poll(self, req: Dict[str, Any], read_only: bool, report_activity: bool = False) -> Dict[str, Any]:
        """Protocol 1.0 check: read the Log Solution result; long-poll up to timeout_sec.

        timeout_sec = 0 reads once. timeout_sec > 0 returns early on taken, paused,
        already_taken or an error, and otherwise times out with the last observed state.
        """
        from log_solution_reader import FINISHED_STATES
        from monitor_flow import MonitorStartError, bring_log_solution_forward

        kvm = req.get("_kvm_client")
        entry = req.get("_device_entry")
        timeout_sec = float(req["timeout_sec"])
        reader = req.get("_reader")
        writer = req.get("_writer")
        start_time = time.monotonic()
        last: Dict[str, Any] = {"kind": "unknown", "state": "", "reason": "", "capacity": 0,
                                "active": [], "results": []}
        # Visibility recovery (check only): when Log Solution stopped being visible, and Dock clicks so far.
        # Only a reliable reading resets the timer; the click count never resets within a request.
        unseen_since: Optional[float] = None
        dock_recoveries = 0

        while True:
            elapsed = time.monotonic() - start_time
            is_client_gone = (reader is not None and reader.at_eof()) or (
                writer is not None and (writer.is_closing() or getattr(writer, "_transport", None) is None)
            )
            if is_client_gone:
                logger.info("Client disconnected during check for device %s", entry.device_id)
                return {"status": "cancelled", "error": "client_disconnected"}
            if not self._is_running or (self._shutdown_event and self._shutdown_event.is_set()):
                return {"status": "error", "error": "server_shutting_down"}

            # Frame Freeze Guard: the picture's timestamps must keep advancing.
            is_live, freeze_err = await self.freeze_guard.verify_liveness(
                kvm,
                device_id=entry.device_id,
                poll_interval=0.05,
            )
            if not is_live:
                return {
                    "status": "error",
                    "error": "frame_frozen",
                    "message": (freeze_err or {}).get("message", "frame presentation timestamp is static"),
                }

            obs, state = await self._observe_check(kvm, entry, req, read_only=read_only,
                                                   report_activity=report_activity)
            if "error" in obs:
                return _check_error(obs["error"], obs.get("message", ""), last)
            last = obs

            if not read_only:
                self._account_round_screen(entry.device_id, state)
            if obs["kind"] == "taken" and obs["capacity"] != entry.channels:
                return {
                    "status": "error",
                    "error": "capacity_mismatch",
                    "message": f"screen shows {obs['capacity']} slots, device is configured for {entry.channels}",
                    "data": {**obs, "kind": "unknown", "reason": "capacity_mismatch", "results": []},
                }
            # Review, then an exact re-view, both win before the round-start request check.
            if obs["kind"] in ("paused", "already_taken"):
                return {"status": "ok", "data": obs}
            if not read_only and obs["kind"] != "unknown":
                refused = self._round_start_refusal(entry.device_id, obs)
                if refused is not None:
                    return refused
            if obs["kind"] == "taken":
                if not read_only:
                    self._round_track(entry.device_id).settle()
                return {"status": "ok", "data": obs}
            # Finished screens carry no active channels (reportable_active), so they never become started.
            if report_activity and obs["kind"] == "waiting" and obs.get("active"):
                return {"status": "ok", "data": {**obs, "kind": "started", "reason": "activity_confirmed"}}
            if read_only and state in FINISHED_STATES:
                return {"status": "ok", "data": obs}
            if timeout_sec == 0:
                return {"status": "ok", "data": obs}
            if elapsed >= timeout_sec:
                return {
                    "status": "timeout",
                    "error": "timeout",
                    "message": f"no reliable result within {timeout_sec:g} s",
                    "data": last,
                }
            if not read_only:
                visibility = _visibility(obs)
                if visibility == "unseen":
                    now = time.monotonic()
                    if unseen_since is None:
                        unseen_since = now
                    if dock_recoveries >= self.max_dock_recoveries:
                        return _check_error("log_solution_not_visible",
                                            f"Log Solution not visible after {dock_recoveries} Dock clicks", last)
                    if now - unseen_since >= self.visibility_lost_sec:
                        dock_recoveries += 1
                        unseen_since = None
                        template_root = self._template_root()
                        try:
                            clicked = await bring_log_solution_forward(kvm, template_root=template_root,
                                                                       diagnostics_key=entry.device_id)
                        except MonitorStartError as exc:
                            return _check_error(exc.code, str(exc), last)
                        if not clicked:
                            return _check_error("log_solution_not_found",
                                                "Log Solution Dock icon is not on screen", last)
                        await asyncio.sleep(self.dock_recheck_sec)
                        continue
                elif visibility == "seen":
                    unseen_since = None
            await asyncio.sleep(min(CHECK_POLL_INTERVAL_SEC, max(0.01, timeout_sec - elapsed)))

    async def _handle_shutdown(self, req: Dict[str, Any]) -> Dict[str, Any]:
        # Schedule the server loop to stop after replying cleanly to the client
        if self._loop and self._shutdown_event:
            self._loop.call_later(0.05, self._shutdown_event.set)
        return {}

    def _validate_request(self, req: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """Protocol 1.0 request validation. Returns (error, message) for the first violation."""
        if req.get("protocol_version") != PROTOCOL_VERSION:
            return "unsupported_protocol_version", f"protocol_version must be '{PROTOCOL_VERSION}'"

        req_id = req.get("req_id")
        if req_id is None or req_id == "":
            return "missing_req_id", "req_id is required"
        if not isinstance(req_id, str):
            return "invalid_request", "req_id must be a string"

        cmd = req.get("cmd")
        if cmd is None or cmd == "":
            return "missing_command", "cmd is required"
        if not isinstance(cmd, str):
            return "invalid_request", "cmd must be a string"

        device = req.get("device")
        if not isinstance(device, str):
            return "invalid_request", "device must be a string"

        if cmd not in self._command_fields:
            return "unknown_command", f"unknown command '{cmd}'"

        allowed = self._command_fields[cmd]
        for key in req:
            if key in COMMON_FIELDS or key in allowed:
                continue
            if key in ("kvm_ip", "station"):
                return "forbidden_field", f"field '{key}' is not allowed; address the device by 'device' ID"
            return "unknown_field", f"field '{key}' is not defined for '{cmd}'"

        if cmd in self._no_device_cmds:
            if device != "":
                return "invalid_argument", f"device must be empty for '{cmd}'"
            return None

        if device == "":
            return "missing_device", f"'{cmd}' requires a 'device' ID"
        if self.device_registry.get(device) is None:
            return "unknown_device", f"device '{device}' is not configured"
        entry = self.device_registry.get(device)
        operations = STATION_OPERATIONS.get(cmd)
        if operations is not None and entry.station not in operations:
            return "unsupported_command", UNSUPPORTED_STATION_MESSAGES[cmd]
        if cmd == "input":
            try:
                parse_items(req.get("items"), entry.channels)
            except DfuError as exc:
                return exc.code, str(exc)
        if cmd == "snapshot":
            fmt = req.get("format")
            if fmt not in ("jpeg", "png"):
                return "invalid_argument", "format must be 'jpeg' or 'png'"
            quality = req.get("quality")
            if isinstance(quality, bool) or not isinstance(quality, int):
                return "invalid_argument", "quality is required and must be an integer"
            if fmt == "png" and quality != 0:
                return "invalid_argument", "quality must be 0 for png"
            if fmt == "jpeg" and not 1 <= quality <= 100:
                return "invalid_argument", "quality must be between 1 and 100 for jpeg"
        if cmd in ("check_finish", "check_start", "inspect"):
            timeout = req.get("timeout_sec")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                return "invalid_argument", "timeout_sec is required and must be a number"
            if not 0 <= timeout <= CHECK_MAX_TIMEOUT_SEC:
                return "invalid_argument", f"timeout_sec must be between 0 and {CHECK_MAX_TIMEOUT_SEC}"
        if cmd == "monitor_start":
            timeout = req.get("timeout_sec")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                return "invalid_argument", "timeout_sec is required and must be a number"
            if not 1 <= timeout <= MONITOR_START_MAX_TIMEOUT_SEC:
                return "invalid_argument", (
                    f"timeout_sec must be between 1 and {MONITOR_START_MAX_TIMEOUT_SEC}")
        return None

    async def _dispatch_command(
        self,
        req_id: str,
        cmd: str,
        req: Dict[str, Any],
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
    ) -> Dict[str, Any]:
        if reader is not None:
            req["_reader"] = reader
        if writer is not None:
            req["_writer"] = writer

        handler = self._handlers[cmd]

        # Check per-device locking
        raw_device = req.get("device")
        device_id: Optional[str] = (
            str(raw_device)
            if raw_device is not None and str(raw_device) != ""
            else None
        )

        # Only the device ID is trusted from the wire: KVM IP and station come from the registry.
        if device_id is not None:
            entry = self.device_registry.get(device_id)
            if entry is not None:
                req["_device_entry"] = entry
                req["kvm_ip"] = entry.kvm_ip

        is_read_only = cmd in ("snapshot", "inspect")

        if device_id is not None and not is_read_only:
            if not self.lock_manager.try_acquire(device_id, req_id=req_id):
                logger.warning(
                    "Device %s busy; rejecting command '%s' (req_id=%s)",
                    device_id,
                    cmd,
                    req_id,
                )
                return {
                    "req_id": req_id,
                    "status": "busy",
                    "error": "device_busy",
                    "device": device_id,
                }

        # Check KVM connection pooling (kvm_ip comes from the device registry)
        # The lease is taken here and released in the finally below, so the KVM stays alive for the whole handler run.
        raw_kvm_ip = req.get("kvm_ip")
        leased_kvm_ip: Optional[str] = None
        if raw_kvm_ip is not None and str(raw_kvm_ip).strip() != "":
            kvm_ip = str(raw_kvm_ip).strip()
            try:
                kvm_client = await self.kvm_pool.acquire_connection(kvm_ip)
                leased_kvm_ip = kvm_ip
                req["_kvm_client"] = kvm_client
            except Exception as exc:
                logger.exception("Failed to establish KVM connection to '%s' (req_id=%s)", kvm_ip, req_id)
                if device_id is not None and not is_read_only:
                    self.lock_manager.release(device_id)
                err_resp = {
                    "req_id": req_id,
                    "status": "error",
                    "error": "kvm_connect_failed",
                    "message": f"Failed to connect to KVM '{kvm_ip}': {exc}",
                }
                if device_id is not None:
                    err_resp["device"] = device_id
                return err_resp

        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(req)
            else:
                result = await asyncio.to_thread(handler, req)

            # If handler explicitly returns an envelope with custom status like busy/error/timeout/cancelled/ok
            if isinstance(result, dict) and result.get("status") in ("ok", "error", "busy", "timeout", "cancelled"):
                resp = dict(result)
                if "req_id" not in resp:
                    resp["req_id"] = req_id
                if "device" not in resp and device_id is not None:
                    resp["device"] = device_id
                return resp

            resp = {
                "req_id": req_id,
                "status": "ok",
                "data": result,
            }
            if device_id is not None:
                resp["device"] = device_id
            return resp
        except Exception as exc:
            logger.exception("Error executing command '%s'", cmd)
            err_resp = {
                "req_id": req_id,
                "status": "error",
                "error": "internal_error",
                "message": str(exc),
            }
            if device_id is not None:
                err_resp["device"] = device_id
            return err_resp
        finally:
            if device_id is not None and not is_read_only:
                self.lock_manager.release(device_id)
            if leased_kvm_ip is not None:
                await self.kvm_pool.release_connection(leased_kvm_ip)

    async def _process_line(
        self,
        raw_line: bytes,
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
    ) -> bytes:
        """Parse one line, validate it, dispatch it, and return a newline-terminated 1.0 envelope."""
        line_str = raw_line.decode("utf-8", errors="replace").strip()
        if not line_str:
            return b""

        started = time.monotonic()
        try:
            req = json.loads(line_str)
        except Exception as exc:
            return self._encode_envelope(started, "", "", "", "error", "invalid_json", f"Failed to parse JSON: {exc}", {})
        if not isinstance(req, dict):
            return self._encode_envelope(started, "", "", "", "error", "invalid_request", "Request must be a JSON object", {})

        req_id = req.get("req_id") if isinstance(req.get("req_id"), str) else ""
        device = req.get("device") if isinstance(req.get("device"), str) else ""
        cmd = req.get("cmd") if isinstance(req.get("cmd"), str) else ""

        invalid = self._validate_request(req)
        if invalid is not None:
            error, message = invalid
            return self._encode_envelope(started, req_id, device, cmd, "error", error, message, {})

        raw = await self._dispatch_command(req_id, cmd, req, reader=reader, writer=writer)
        if raw.get("error") == "client_disconnected":
            return b""  # PROTOCOL 7: the client is gone, so nothing is written
        return self._encode_result(started, req_id, device, cmd, raw)

    def _encode_result(
        self, started: float, req_id: str, device: str, cmd: str, raw: Dict[str, Any]
    ) -> bytes:
        """Map a handler result (ok/error/busy/timeout/...) onto the fixed 1.0 envelope."""
        status = str(raw.get("status", "ok"))
        if status in ("busy", "cancelled"):
            status = "error"
        if isinstance(raw.get("data"), dict):
            payload = raw["data"]
        elif "data" in raw:
            payload = {"value": raw["data"]}
        else:
            payload = {k: v for k, v in raw.items() if k not in ENVELOPE_KEYS}

        error = ""
        message = str(raw.get("message", ""))
        if status == "timeout":
            error = "timeout"
        elif status != "ok":
            error = ERROR_CODE_MAP.get(str(raw.get("error") or "internal_error"), str(raw.get("error") or "internal_error"))
        return self._encode_envelope(started, req_id, device, cmd, status, error, message, payload)

    def _encode_envelope(
        self,
        started: float,
        req_id: str,
        device: str,
        cmd: str,
        status: str,
        error: str,
        message: str,
        payload: Dict[str, Any],
    ) -> bytes:
        data = {**DEFAULT_DATA.get(cmd, {}), **payload}
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "req_id": req_id,
            "device": device,
            "cmd": cmd,
            "status": status,
            "error": error if status != "ok" else "",
            "message": message if status != "ok" else "",
            "elapsed_sec": round(time.monotonic() - started, 3),
            "data": data,
        }
        return (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")

    async def _client_connected_cb(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle individual client connection with newline-delimited stream."""
        self._active_writers.add(writer)
        peer = writer.get_extra_info("peername")
        logger.info("Client connected: %s", peer)

        try:
            while not reader.at_eof() and self._is_running:
                try:
                    line = await reader.readline()
                except (ConnectionError, asyncio.IncompleteReadError):
                    break
                if not line:
                    break

                response_bytes = await self._process_line(line, reader=reader, writer=writer)
                if response_bytes:
                    writer.write(response_bytes)
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        except Exception:
            logger.exception("Unexpected error in client session with %s", peer)
        finally:
            self._active_writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client disconnected: %s", peer)

    async def _run_server_async(self) -> None:
        """Async main loop for starting and running the server."""
        self._loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()

        # Handle termination signals gracefully
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._loop.add_signal_handler(sig, self._shutdown_event.set)
            except (NotImplementedError, RuntimeError):
                pass

        self._server = await asyncio.start_server(
            self._client_connected_cb,
            host=self.host,
            port=self.port,
        )

        sockets = self._server.sockets
        if sockets:
            sock_name = sockets[0].getsockname()
            self.server_address = (sock_name[0], sock_name[1])
            self.port = sock_name[1]

        self._is_running = True
        self._start_time = time.time()
        await self.kvm_pool.start()
        self._start_event.set()

        logger.info("TcpJsonServer listening on %s:%s", *self.server_address)

        # Wait until shutdown signal
        await self._shutdown_event.wait()

        logger.info("TcpJsonServer shutting down...")
        self._is_running = False

        # Close all active KVM connections in pool
        await self.kvm_pool.close_all()

        # Close listening socket
        self._server.close()
        await self._server.wait_closed()

        # Close all active client connections
        for writer in list(self._active_writers):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._active_writers.clear()

        logger.info("TcpJsonServer stopped cleanly.")

    def start(self) -> None:
        """Start the server in a background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._start_event.clear()

        def _worker():
            asyncio.run(self._run_server_async())

        self._thread = threading.Thread(target=_worker, name="TcpJsonServerThread", daemon=True)
        self._thread.start()

        if not self._start_event.wait(timeout=5.0):
            raise TimeoutError("TcpJsonServer failed to start within 5.0 seconds")

    def stop(self) -> None:
        """Request graceful shutdown of the server."""
        if not self._is_running:
            return
        if self._loop and self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)

    def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for the server background thread to finish."""
        if self._thread:
            self._thread.join(timeout=timeout)

    def serve_forever(self) -> None:
        """Run the server synchronously in the current thread (blocks until Ctrl-C or shutdown)."""
        try:
            asyncio.run(self._run_server_async())
        except (KeyboardInterrupt, SystemExit):
            pass


class RoundTrack:
    """One device's round-start request for the life of this Core process."""

    def __init__(self) -> None:
        self.start_requested = False
        self.monitoring_seen = False
        self.input_released = False

    def note_start(self) -> None:
        """Shortcut sent, or monitor_start is looking at Preparing. Keeps an existing request."""
        self.start_requested = True

    def note_monitoring(self) -> None:
        """This monitor_start call observed Monitoring, so the round is ready to judge."""
        self.monitoring_seen = True
        self.input_released = False

    def begin_start(self) -> None:
        """A new monitor_start. Keeps a request that monitor_not_started did not clear."""
        self.monitoring_seen = False
        self.input_released = False

    def settle(self) -> None:
        """taken or manual_verification_required spends the request and releases the next SN input."""
        self.start_requested = False
        self.monitoring_seen = False
        self.input_released = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless TCP JSON Server for B518 JetKVM Relay")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--devices-file", default=None, help="Device list JSON (default: ~/Documents/template/devices.json)")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--log-file", default=None, help="Path to rotating log file")
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024, help="Max log file size before rotation")
    parser.add_argument("--backup-count", type=int, default=5, help="Number of rotating backup log files to retain")
    args = parser.parse_args()

    if args.log_file:
        try:
            from core_daemon import setup_rotating_logging
            setup_rotating_logging(
                log_file=args.log_file,
                log_level=args.log_level,
                max_bytes=args.max_bytes,
                backup_count=args.backup_count,
            )
        except ImportError:
            logging.basicConfig(
                level=getattr(logging, args.log_level.upper(), logging.INFO),
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
    else:
        logging.basicConfig(
            level=getattr(logging, args.log_level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    try:
        registry = DeviceRegistry.load(args.devices_file)
    except DeviceRegistryError as exc:
        for line in exc.errors:
            logging.error("Device list error: %s", line)
        raise SystemExit(1) from exc

    server = TcpJsonServer(host=args.host, port=args.port, device_registry=registry)
    print(f"Starting Headless TCP JSON Server on {args.host}:{args.port}...")
    server.serve_forever()


if __name__ == "__main__":
    main()
