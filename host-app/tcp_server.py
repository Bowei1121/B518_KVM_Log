"""Headless TCP JSON Protocol & Multiplexing Server.

Provides a standalone, headless TCP server that communicates with LabVIEW and
other clients using newline-delimited JSON. Supports concurrent multiplexed
client connections, req_id correlation, and graceful shutdown.
"""

from __future__ import annotations

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Coroutine, Dict, Mapping, Optional, Set, Tuple

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
    from device_health import DeviceHealth, build_health
except ImportError:
    from .device_health import DeviceHealth, build_health  # type: ignore[import-not-found,no-redef]

try:
    from monitor_flow import MONITOR_CONFIRM_SEC
except ImportError:
    from .monitor_flow import MONITOR_CONFIRM_SEC  # type: ignore[import-not-found,no-redef]

MONITOR_START_MAX_TIMEOUT_SEC = 300

try:
    from device_slot import DeviceSlot
    from round_check import CheckMode, ClickDock, Reading, Reply, RoundCheck
except ImportError:
    from .device_slot import DeviceSlot  # type: ignore[import-not-found,no-redef]
    from .round_check import (  # type: ignore[import-not-found,no-redef]
        CheckMode, ClickDock, Reading, Reply, RoundCheck,
    )

try:
    from reading_stats import empty_report
except ImportError:
    from .reading_stats import empty_report  # type: ignore[import-not-found,no-redef]

try:
    from hid_session import FlowError
    from bt_flow import run_start_all
    from dfu_flow import parse_items, run_dfu_input, run_dfu_ok
except ImportError:
    from .hid_session import FlowError  # type: ignore[import-not-found,no-redef]
    from .bt_flow import run_start_all  # type: ignore[import-not-found,no-redef]
    from .dfu_flow import parse_items, run_dfu_input, run_dfu_ok  # type: ignore[import-not-found,no-redef]

logger = logging.getLogger("TcpJsonServer")


def _flow_log(en, zh=None):
    """流程回呼：英文是 Log 本文，中文放進 extra["zh"]（issue #106 雙語格式）。"""
    logger.info(en, extra={"zh": en if zh is None else zh})

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
def _unknown(reason: str) -> Dict[str, Any]:
    return {"kind": "unknown", "state": "", "reason": reason, "capacity": 0, "active": [], "results": []}


def _no_args(req: Dict[str, Any], entry: Any) -> None:
    """``parse`` for a command that takes no fields beyond the common ones."""
    return None


def _timeout_arg(req: Dict[str, Any], low: int, high: int) -> float:
    """``timeout_sec`` within [low, high] as a float; otherwise FlowError(invalid_argument)."""
    timeout = req.get("timeout_sec")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise FlowError("invalid_argument", "timeout_sec is required and must be a number")
    if not low <= timeout <= high:
        raise FlowError("invalid_argument", f"timeout_sec must be between {low} and {high}")
    return float(timeout)


def _check_timeout(req: Dict[str, Any], entry: Any) -> float:
    return _timeout_arg(req, 0, CHECK_MAX_TIMEOUT_SEC)


def _monitor_timeout(req: Dict[str, Any], entry: Any) -> float:
    return _timeout_arg(req, 1, MONITOR_START_MAX_TIMEOUT_SEC)


def _snapshot_args(req: Dict[str, Any], entry: Any) -> Tuple[str, int]:
    """``(format, quality)`` for snapshot, checked before the KVM is touched."""
    fmt = req.get("format")
    if fmt not in ("jpeg", "png"):
        raise FlowError("invalid_argument", "format must be 'jpeg' or 'png'")
    quality = req.get("quality")
    if isinstance(quality, bool) or not isinstance(quality, int):
        raise FlowError("invalid_argument", "quality is required and must be an integer")
    if fmt == "png" and quality != 0:
        raise FlowError("invalid_argument", "quality must be 0 for png")
    if fmt == "jpeg" and not 1 <= quality <= 100:
        raise FlowError("invalid_argument", "quality must be between 1 and 100 for jpeg")
    return fmt, quality


def _input_items(req: Dict[str, Any], entry: Any) -> Any:
    """The validated ``items``. run_dfu_input parses them once more: that is dfu_flow's own entry point."""
    parse_items(req.get("items"), entry.channels)
    return req.get("items")


@dataclass(frozen=True)
class CommandSpec:
    """Every rule of one protocol command: what validation accepts, how dispatch runs it, what the reply carries.

    ``parse(req, entry)`` runs once during validation (``entry`` is None for a device-less command). It returns
    the args the handler gets, or raises ``FlowError(invalid_argument)``. ``handler(ctx, args)`` is the work.
    ``stations`` maps station → the operation for that station (``ctx.operation``); None accepts every station.
    ``takes_lock`` False is a read-only device command that never returns ``device_busy``.
    ``default_data`` fills the ``data`` of every reply for this command.
    """
    name: str
    handler: Callable[..., Any]
    fields: frozenset = frozenset()
    device_bound: bool = False
    takes_lock: bool = True
    stations: Optional[Mapping[str, Callable[..., Any]]] = None
    parse: Callable[[Dict[str, Any], Any], Any] = _no_args
    default_data: Mapping[str, Any] = field(default_factory=dict)
    unsupported_message: str = ""
    # True when the handler reads ctx.slot. Creating a slot also creates the device's FrameStream,
    # so commands that never used it (snapshot) must not create it.
    needs_slot: bool = False


@dataclass(frozen=True)
class CommandContext:
    """What a handler may use. Device-less commands leave every device field None."""
    entry: Any = None
    kvm: Any = None
    slot: Optional[DeviceSlot] = None
    operation: Optional[Callable[..., Any]] = None
    reader: Any = None
    writer: Any = None


@dataclass(frozen=True)
class _Admitted:
    """A request that passed validation: its spec, its device entry (None if device-less), and its parsed args."""
    spec: CommandSpec
    entry: Any
    args: Any


def _inspect_round(kvm: Any, tracker: Any, stream: Any = None, heartbeat: Any = None,
                   stats: Any = None) -> Tuple[Dict[str, Any], Any]:
    """Read-only reading of the Log Solution screen. Runs on a worker thread.

    Returns ``(reply, state)``. Reports what the screen shows now. Never returns taken and never
    arms or consumes the device's round gate. Two agreeing fresh frames fill ``active``.
    With ``stream``, the reading also observes the device's FrameStream: a frozen picture is frame_frozen.
    With ``heartbeat``, the device's HeartbeatTracker judges the display: stopped is log_solution_heartbeat_stopped.
    """
    from frame_stream import FRESHNESS_PENDING
    from log_solution_heartbeat import HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE, Step, cells_of
    from log_solution_reader import (
        CONFIRMATION_REQUIRED, ScreenKind, reportable_active, screen_kind,
    )
    from reading_stats import read_app_frame_recorded

    snapshot, obs = read_app_frame_recorded(kvm, stats)
    if stream is not None:
        verdict = stream.observe_snapshot(snapshot)
        if verdict.kind == "frozen":
            if heartbeat is not None:
                heartbeat.reset()
            return {"error": "frame_frozen", "message": verdict.message}, None
        if not verdict.advanced:
            # PTS has not been seen to move: not judged, and the tracker does not see this frame.
            return _unknown(FRESHNESS_PENDING), None
    if snapshot is None:
        return {"error": "frame_frozen", "message": "No frame received from KVM yet"}, None
    if heartbeat is not None:
        step = heartbeat.observe(snapshot.stream_id, snapshot.sequence, snapshot.received_monotonic, cells_of(obs))
        if step is Step.STOPPED:
            return {"error": HEARTBEAT_STOPPED, "message": HEARTBEAT_STOPPED_MESSAGE}, None
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


def _kvm_lost(ctx: CommandContext) -> Dict[str, Any]:
    """check reply when the KVM died during the command (protocol error kvm_connect_failed, ADR-0001 補註 #58)."""
    return {
        "status": "error",
        "error": "kvm_connect_failed",
        "message": f"KVM '{ctx.entry.kvm_ip}' connection lost during the command",
    }


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

        # Runtime records are read/written only on the asyncio event-loop thread.
        # _device_health has no await, so readers never see a partial update.
        # Future worker-thread updates must add an internal lock, not the Device Lock.
        self._health: dict[str, DeviceHealth] = {
            record.device: record
            for record in build_health(self.device_registry.entries(), [], [], [])
        }

        self._server: Optional[asyncio.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_event = threading.Event()
        self._shutdown_event: Optional[asyncio.Event] = None

        self._active_writers: Set[asyncio.StreamWriter] = set()
        self._slots: Dict[str, DeviceSlot] = {}
        self.inspection_timeout_sec = float(inspection_timeout_sec)
        self.monitor_confirm_sec = float(monitor_confirm_sec)
        self.visibility_lost_sec = float(visibility_lost_sec)
        self.dock_recheck_sec = float(dock_recheck_sec)
        self.max_dock_recoveries = int(max_dock_recoveries)
        # Frame readings run on worker threads so the event loop keeps serving requests.
        self._inspector = ThreadPoolExecutor(max_workers=16, thread_name_prefix="inspect")
        self._commands: Dict[str, CommandSpec] = {}

        self._start_time: float = 0.0
        self._is_running = False

        self._register_default_handlers()

    @property
    def is_running(self) -> bool:
        """Return True if the server is currently running."""
        return self._is_running

    def register_handler(self, spec: CommandSpec) -> None:
        """Add or replace one command in the table. Production commands come from ``_default_specs``."""
        self._commands[spec.name] = spec

    def _register_default_handlers(self) -> None:
        for spec in self._default_specs():
            self.register_handler(spec)

    def _default_specs(self) -> list[CommandSpec]:
        """The protocol 1.0 command table: one CommandSpec per command, the only place its rules live."""
        return [
            CommandSpec("ping", self._handle_ping, default_data={"pong": False, "core_version": ""}),
            CommandSpec("status", self._handle_status, default_data={"core_uptime_sec": 0, "devices": []}),
            CommandSpec("core_status", self._handle_core_status),
            CommandSpec("shutdown", self._handle_shutdown),
            CommandSpec("snapshot", self._handle_snapshot, fields=frozenset({"format", "quality"}),
                        device_bound=True, takes_lock=False, parse=_snapshot_args,
                        default_data={"width": 0, "height": 0, "format": "", "image_base64": ""}),
            CommandSpec("check_start", self._handle_check_start, fields=frozenset({"timeout_sec"}),
                        device_bound=True, parse=_check_timeout, default_data=_unknown(""), needs_slot=True),
            CommandSpec("check_finish", self._handle_check_finish, fields=frozenset({"timeout_sec"}),
                        device_bound=True, parse=_check_timeout, default_data=_unknown(""), needs_slot=True),
            CommandSpec("inspect", self._handle_inspect, fields=frozenset({"timeout_sec"}),
                        device_bound=True, takes_lock=False, parse=_check_timeout, default_data=_unknown(""),
                        needs_slot=True),
            CommandSpec("input", self._handle_input, fields=frozenset({"items"}), device_bound=True,
                        stations={"DFU": run_dfu_input}, parse=_input_items, default_data={"entered": []},
                        unsupported_message="'input' is only supported for DFU devices"),
            CommandSpec("start_test", self._handle_start_test, device_bound=True,
                        stations={"DFU": run_dfu_ok, "BT": run_start_all}, default_data={"action": ""},
                        unsupported_message=(
                            "'start_test' is not supported for FCT; FCT starts when the fixture is pushed in")),
            CommandSpec("monitor_start", self._handle_monitor_start, fields=frozenset({"timeout_sec"}),
                        device_bound=True, parse=_monitor_timeout, default_data={"state": "", "capacity": 0},
                        needs_slot=True),
        ]

    async def _handle_ping(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        return {"pong": True, "core_version": CORE_VERSION}

    def _device_health(self) -> list[DeviceHealth]:
        """Update stored health records and return copies in registry order.

        Reads Device Lock, Freeze Guard and the pool once each. Reads no frame and takes no device lock.
        Both core_status and LabVIEW status are built from these records.
        """
        active = set(self.lock_manager.get_active_devices())
        frozen = set(self.freeze_guard.get_status().get("frozen_devices", []))
        connected = set(self.kvm_pool.get_active_ips())
        for record in self._health.values():
            record.refresh(active, frozen, connected)
        return [replace(record) for record in self._health.values()]

    def _station_rows(self) -> list[Dict[str, Any]]:
        """Real-time status of every configured test station (the ``stations`` list of core_status).

        Reads no frame and takes no device lock: the reading fields are whatever readings already recorded.
        """
        stations_list: list[Dict[str, Any]] = []
        for h in self._device_health():
            # Reading statistics come from the slot's recorded readings. A device without a slot has none yet.
            slot = self._slots.get(str(h.device))
            reading = slot.stats.report() if slot is not None else empty_report()
            if not h.connected:
                # The last frame size belongs to a picture that is gone: a disconnected device has no output size.
                reading["frame_size"] = None

            stations_list.append({
                "station": h.station,
                "device": h.device,
                "kvm_ip": h.kvm_ip,
                "status": h.status,
                "is_busy": h.busy,
                "is_frozen": h.frozen,
                "is_connected": h.connected,
                **reading,
            })
        return stations_list

    async def _handle_status(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        """LabVIEW status: Core uptime and per-device state. KVM IPs are never returned."""
        uptime = time.time() - self._start_time if self._start_time else 0.0
        devices: list[Dict[str, Any]] = []
        for h in self._device_health():
            devices.append({
                "device": h.device,
                "station": h.station,
                "connected": h.connected,
                "busy": h.busy,
                "frozen": h.frozen,
                "channels": h.channels,
            })
        return {"core_uptime_sec": int(uptime), "devices": devices}

    async def _handle_core_status(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        """Full internal Core state for Monitor UI / admin tools (not part of the LabVIEW protocol)."""
        uptime = time.time() - self._start_time if self._start_time else 0.0
        active_devices = self.lock_manager.get_active_devices()
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
            "stations": self._station_rows(),
            "core_version": CORE_VERSION,
        }

    async def _handle_snapshot(self, ctx: CommandContext, args: Tuple[str, int]) -> Dict[str, Any]:
        """Request and return a single frame snapshot as base64 without acquiring device locks."""
        fmt, quality = args
        latest = ctx.kvm.latest_frame()
        if latest is None:
            return {
                "status": "error",
                "error": "no_frame",
                "message": "No frame received from KVM yet",
            }
        frame, pts = latest.image, latest.pts

        # Encode to JPEG/PNG base64
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
            logger.warning("Failed to encode frame to base64: %s", exc, extra={"zh": "影格轉 base64 失敗: %s"})

        return {
            "status": "ok",
            "width": width,
            "height": height,
            "format": fmt,
            "image_base64": img_b64,
        }

    def _slot(self, device_id: Any) -> DeviceSlot:
        """The device's slot (round, gate, inspect tracker, in-flight readings), created on first use."""
        key = str(device_id)
        slot = self._slots.get(key)
        if slot is None:
            # setdefault: _log_solution_refusal calls this from a worker thread, so one slot must win.
            slot = self._slots.setdefault(key, DeviceSlot(key, self.freeze_guard.stream(key)))
        return slot

    def _device_round(self, device_id: Any) -> Any:
        """The device's DeviceRound: the test helpers and protocol tests read the round through the server."""
        return self._slot(device_id).round

    async def _read_reading(self, slot: DeviceSlot, kvm: Any, mode: CheckMode) -> Reading:
        """One reading of the Log Solution screen on a worker thread, within the per-frame time limit.

        A reading that exceeds the limit is unknown and the poll continues. A lane whose previous reading
        is still running is unknown without starting another. check_start and check_finish share one lane.
        """
        lane = "inspect" if mode is CheckMode.INSPECT else "check"
        previous = slot.flights.get(lane)
        if previous is not None and not previous.done():
            return Reading.unknown("inspection_busy")

        future = self._inspector.submit(self._reading_job(slot, kvm, mode))
        slot.flights[lane] = future
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=self.inspection_timeout_sec)
        except asyncio.TimeoutError:
            logger.debug("Inspection for %s exceeded %.3f s; reported as unknown", slot.device_id,
                         self.inspection_timeout_sec,
                         extra={"zh": "%s 檢視逾時（超過 %.3f 秒），回報為未知"})
            return Reading.unknown("inspection_timeout")
        except Exception as exc:
            logger.exception("Inspection error for %s", slot.device_id, extra={"zh": "%s 檢視發生錯誤"})
            return Reading.failed("internal_error", f"inspection error: {exc}")

    @staticmethod
    def _reading_job(slot: DeviceSlot, kvm: Any, mode: CheckMode) -> Callable[[], Reading]:
        """The worker-thread job for one reading.

        Each reader asks FrameStream first, then judges. RoundCheck only sees the resulting Reading.
        """
        if mode is CheckMode.INSPECT:
            return lambda: Reading.from_inspect(
                *_inspect_round(kvm, slot.inspect_tracker, slot.stream, slot.heartbeat, slot.stats))
        report_active = mode is CheckMode.CHECK_START

        def job() -> Reading:
            from round_frame_consumer import observe_latest_round_frame
            decision = observe_latest_round_frame(kvm, slot.gate)
            return Reading.from_decision(decision, slot.gate.last_active if report_active else None)
        return job

    async def _picture_refusal(self, ctx: CommandContext) -> Optional[Tuple[str, str]]:
        """Why a HID-sending command must not run, as (code, message), before any HID (ADR-0005 2026-10-20 第 3 點).

        Order: KVM health, then one FrameStream window (live PTS advance within the threshold passes at once),
        then the command's own Log Solution refusal. No frame is left to that refusal, as before.
        """
        from frame_stream import FROZEN

        kvm = ctx.kvm

        def lost() -> Tuple[str, str]:
            reply = _kvm_lost(ctx)
            return reply["error"], reply["message"]

        if not kvm.is_healthy():
            return lost()
        verdict = await self.freeze_guard.stream(ctx.entry.device_id).wait_live(kvm.latest_frame)
        if not kvm.is_healthy():
            return lost()
        if verdict.kind == FROZEN and verdict.reason != "no_frame":
            return "frame_frozen", verdict.message
        refusal = await self._heartbeat_refusal(ctx)
        # The probe samples for up to 2.5 s: a KVM that died meanwhile is kvm_connect_failed, not a later error.
        if not kvm.is_healthy():
            return lost()
        return refusal

    async def _heartbeat_refusal(self, ctx: CommandContext) -> Optional[Tuple[str, str]]:
        """Why a HID-sending command must not run because the Log Solution display stopped (issue #77).

        A change seen within RECENT_CHANGE_SEC passes at once. Otherwise the picture is sampled for up to
        PROBE_MAX_SEC on a worker thread; only three identical counted readings refuse.
        """
        from log_solution_heartbeat import HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE, probe
        from reading_stats import read_snapshot_recorded

        slot = self._slot(ctx.entry.device_id)
        tracker = slot.heartbeat
        if tracker.changed_within(time.monotonic()):
            return None
        stopped = await asyncio.wrap_future(self._inspector.submit(
            probe, tracker, ctx.kvm.latest_frame, lambda snapshot: read_snapshot_recorded(snapshot, slot.stats)))
        return (HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE) if stopped else None

    def _template_root(self) -> Any:
        """Configured template root, or TemplateCatalog.default_root() (~/Documents/template)."""
        from template_catalog import TemplateCatalog

        return self.template_root or TemplateCatalog.default_root()

    def _log_solution_refusal(self, kvm: Any, entry: Any) -> Optional[Tuple[str, str]]:
        """Why SN input must wait, as (code, message): None when the Log Solution is in standby or its round was taken."""
        from log_solution_reader import MarkerState
        from reading_stats import read_latest_app_frame_recorded

        frame, observation = read_latest_app_frame_recorded(kvm, self._slot(entry.device_id).stats)
        if frame is None:
            return "hid_failed", "no frame to read the Log Solution state"
        if not observation.reliable:
            return "hid_failed", "Log Solution state unreadable"
        if observation.state == MarkerState.STANDBY:
            return None
        # complete and manual_stop_complete are both READY. Input waits until this round
        # was delivered or manual_verification_required was already returned.
        if observation.finished:
            if self._slot(entry.device_id).round.released_for_input():
                return None
            return "log_solution_busy", "round result not yet taken"
        return "log_solution_busy", f"Log Solution state is {observation.state.value}"

    def _monitor_refusal(self, kvm: Any, stats: Any = None) -> Optional[Tuple[str, str]]:
        """Why start_test must wait, as (code, message): None when a reliable Log Solution frame reads Monitoring."""
        from log_solution_reader import MarkerState
        from reading_stats import read_latest_app_frame_recorded

        frame, observation = read_latest_app_frame_recorded(kvm, stats)
        if frame is None:
            return "monitor_not_ready", "Log Solution is not monitoring (no frame); no HID sent"
        if observation.reliable and observation.state == MarkerState.MONITORING:
            return None
        state = observation.state.value if observation.reliable else "unreadable"
        return "monitor_not_ready", f"Log Solution is not monitoring ({state}); no HID sent"

    async def _handle_start_test(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        """start_test 1.0: DFU clicks OK, BT clicks Start All. Both only while Log Solution is monitoring."""
        refusal = await self._picture_refusal(ctx)
        if refusal is None:
            refusal = await asyncio.wrap_future(
                self._inspector.submit(self._monitor_refusal, ctx.kvm, self._slot(ctx.entry.device_id).stats))
        if refusal is not None:
            code, message = refusal
            return {"status": "error", "error": code, "message": message}

        try:
            result = await ctx.operation(ctx.kvm, template_root=self._template_root(),
                                         log=_flow_log, diagnostics_key=ctx.entry.device_id)
        except FlowError as exc:
            return {"status": "error", "error": exc.code, "message": str(exc)}
        return {"status": "ok", "data": result}

    async def _handle_monitor_start(self, ctx: CommandContext, args: float) -> Dict[str, Any]:
        """monitor_start 1.0: open Log Solution from the Dock and start monitoring (all stations)."""
        from monitor_flow import run_monitor_start

        # Before begin_start: a frozen refusal must not drop the previous delivery or the input release.
        refusal = await self._picture_refusal(ctx)
        if refusal is not None:
            code, message = refusal
            return {"status": "error", "error": code, "message": message}
        slot = ctx.slot
        slot.round.begin_start()
        template_root = self._template_root()
        try:
            result = await run_monitor_start(
                ctx.kvm, slot.gate, template_root=template_root,
                confirm_sec=self.monitor_confirm_sec, start_timeout_sec=args,
                log=_flow_log, diagnostics_key=ctx.entry.device_id,
                on_preparing=slot.round.note_start, on_shortcut=slot.round.note_start)
        except FlowError as exc:
            # monitor_not_started keeps the round-start request. Only a later taken or
            # manual_verification_required reply clears it.
            return {"status": "error", "error": exc.code, "message": str(exc)}
        return {"status": "ok", "data": result}

    async def _handle_input(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        """DFU input 1.0: refuse while a round runs, otherwise enter SNs by channel."""
        refusal = await self._picture_refusal(ctx)
        if refusal is None:
            refusal = await asyncio.wrap_future(
                self._inspector.submit(self._log_solution_refusal, ctx.kvm, ctx.entry))
        if refusal is not None:
            code, message = refusal
            return {"status": "error", "error": code, "message": message}

        try:
            result = await ctx.operation(ctx.kvm, ctx.entry.channels, args, template_root=self._template_root(),
                                         log=_flow_log, diagnostics_key=ctx.entry.device_id)
        except FlowError as exc:
            return {"status": "error", "error": exc.code, "message": str(exc), "data": {"entered": exc.entered}}
        return {"status": "ok", "data": result}

    async def _handle_check_start(self, ctx: CommandContext, args: float) -> Dict[str, Any]:
        """check_start: two fresh frames of activity, or a finished round delivered once."""
        return await self._long_poll(ctx, args, CheckMode.CHECK_START)

    async def _handle_check_finish(self, ctx: CommandContext, args: float) -> Dict[str, Any]:
        return await self._long_poll(ctx, args, CheckMode.CHECK_FINISH)

    async def _handle_inspect(self, ctx: CommandContext, args: float) -> Dict[str, Any]:
        """Monitor UI's read-only check: no device lock, and it never consumes the round result."""
        return await self._long_poll(ctx, args, CheckMode.INSPECT)

    async def _long_poll(self, ctx: CommandContext, timeout_sec: float, mode: CheckMode) -> Dict[str, Any]:
        """Protocol 1.0 check: read the Log Solution result; long-poll up to timeout_sec.

        timeout_sec = 0 reads once. timeout_sec > 0 returns early on taken, paused,
        already_taken or an error, and otherwise times out with the last observed state.
        The judgement order and Dock recovery live in RoundCheck; this loop does the I/O.
        """
        from monitor_flow import bring_log_solution_forward

        kvm = ctx.kvm
        entry = ctx.entry
        reader = ctx.reader
        writer = ctx.writer
        slot = ctx.slot
        check = RoundCheck(
            mode, entry.channels, timeout_sec, time.monotonic(),
            round_state=None if mode is CheckMode.INSPECT else slot.round,
            proof_wait_sec=self.freeze_guard.freeze_threshold + CHECK_POLL_INTERVAL_SEC,
            poll_sec=CHECK_POLL_INTERVAL_SEC,
            visibility_lost_sec=self.visibility_lost_sec,
            max_dock_recoveries=self.max_dock_recoveries,
        )

        while True:
            is_client_gone = (reader is not None and reader.at_eof()) or (
                writer is not None and (writer.is_closing() or getattr(writer, "_transport", None) is None)
            )
            if is_client_gone:
                logger.info("Client disconnected during check for device %s", entry.device_id,
                        extra={"zh": "檢查期間用戶端已中斷，設備 %s"})
                return {"status": "cancelled", "error": "client_disconnected"}
            if not self._is_running or (self._shutdown_event and self._shutdown_event.is_set()):
                return {"status": "error", "error": "server_shutting_down"}
            # KVM died mid-command (track ended or peer failed): report now, without waiting for the freeze.
            if not kvm.is_healthy():
                return _kvm_lost(ctx)

            reading = await self._read_reading(slot, kvm, mode)
            if reading.error is not None:
                code, message = reading.error
                # The KVM can die while its last picture is frozen: a lost connection, not a frozen picture.
                if code == "frame_frozen" and not kvm.is_healthy():
                    return _kvm_lost(ctx)
                return check.fail(code, message)

            outcome = check.step(reading, time.monotonic())
            if isinstance(outcome, Reply):
                return outcome.payload
            if isinstance(outcome, ClickDock):
                try:
                    clicked = await bring_log_solution_forward(kvm, template_root=self._template_root(),
                                                               diagnostics_key=entry.device_id, stats=slot.stats)
                except FlowError as exc:
                    return check.fail(exc.code, str(exc))
                if not clicked:
                    return check.fail("log_solution_not_found", "Log Solution Dock icon is not on screen")
                await asyncio.sleep(self.dock_recheck_sec)
                continue
            await asyncio.sleep(outcome.sleep_sec)

    async def _handle_shutdown(self, ctx: CommandContext, args: Any) -> Dict[str, Any]:
        # Schedule the server loop to stop after replying cleanly to the client
        if self._loop and self._shutdown_event:
            self._loop.call_later(0.05, self._shutdown_event.set)
        return {}

    def _validate_request(self, req: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """Protocol 1.0 validation only: (error, message) for the first violation, or None."""
        return self._admit(req)[0]

    def _admit(self, req: Dict[str, Any]) -> Tuple[Optional[Tuple[str, str]], Optional[_Admitted]]:
        """Protocol 1.0 request validation against the command table.

        Returns ``((error, message), None)`` for the first violation, or ``(None, _Admitted)``. Each command's
        ``parse`` runs once here; the handler gets its result as args.
        """
        if req.get("protocol_version") != PROTOCOL_VERSION:
            return ("unsupported_protocol_version", f"protocol_version must be '{PROTOCOL_VERSION}'"), None

        req_id = req.get("req_id")
        if req_id is None or req_id == "":
            return ("missing_req_id", "req_id is required"), None
        if not isinstance(req_id, str):
            return ("invalid_request", "req_id must be a string"), None

        cmd = req.get("cmd")
        if cmd is None or cmd == "":
            return ("missing_command", "cmd is required"), None
        if not isinstance(cmd, str):
            return ("invalid_request", "cmd must be a string"), None

        device = req.get("device")
        if not isinstance(device, str):
            return ("invalid_request", "device must be a string"), None

        spec = self._commands.get(cmd)
        if spec is None:
            return ("unknown_command", f"unknown command '{cmd}'"), None

        for key in req:
            if key in COMMON_FIELDS or key in spec.fields:
                continue
            if key in ("kvm_ip", "station"):
                return ("forbidden_field", f"field '{key}' is not allowed; address the device by 'device' ID"), None
            return ("unknown_field", f"field '{key}' is not defined for '{cmd}'"), None

        entry = None
        if not spec.device_bound:
            if device != "":
                return ("invalid_argument", f"device must be empty for '{cmd}'"), None
        else:
            if device == "":
                return ("missing_device", f"'{cmd}' requires a 'device' ID"), None
            entry = self.device_registry.get(device)
            if entry is None:
                return ("unknown_device", f"device '{device}' is not configured"), None
            if spec.stations is not None and entry.station not in spec.stations:
                return ("unsupported_command", spec.unsupported_message), None

        try:
            args = spec.parse(req, entry)
        except FlowError as exc:
            return (exc.code, str(exc)), None
        return None, _Admitted(spec, entry, args)

    async def _dispatch_command(
        self,
        req_id: str,
        admitted: _Admitted,
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
    ) -> Dict[str, Any]:
        spec = admitted.spec
        entry = admitted.entry
        # Check per-device locking. Only the device ID is trusted from the wire: KVM IP and station come from the registry.
        device_id: Optional[str] = entry.device_id if entry is not None else None

        if device_id is not None and spec.takes_lock:
            if not self.lock_manager.try_acquire(device_id):
                logger.warning(
                    "Device %s busy; rejecting command '%s' (req_id=%s)",
                    device_id,
                    spec.name,
                    req_id,
                    extra={"zh": "設備 %s 忙碌中，拒絕指令 '%s' (req_id=%s)"},
                )
                return {
                    "req_id": req_id,
                    "status": "busy",
                    "error": "device_busy",
                    "device": device_id,
                }

        # Check KVM connection pooling (kvm_ip comes from the device registry)
        # The lease is taken here and released in the finally below, so the KVM stays alive for the whole handler run.
        raw_kvm_ip = entry.kvm_ip if entry is not None else None
        leased_kvm_ip: Optional[str] = None
        kvm_client: Any = None
        if raw_kvm_ip is not None and str(raw_kvm_ip).strip() != "":
            kvm_ip = str(raw_kvm_ip).strip()
            try:
                kvm_client = await self.kvm_pool.acquire_connection(kvm_ip)
                leased_kvm_ip = kvm_ip
            except Exception as exc:
                logger.exception("Failed to establish KVM connection to '%s' (req_id=%s)", kvm_ip, req_id,
                                 extra={"zh": "建立 KVM 連線失敗 '%s' (req_id=%s)"})
                if device_id is not None and spec.takes_lock:
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

        ctx = CommandContext(
            entry=entry,
            kvm=kvm_client,
            slot=self._slot(device_id) if device_id is not None and spec.needs_slot else None,
            operation=spec.stations[entry.station] if spec.stations is not None else None,
            reader=reader,
            writer=writer,
        )
        try:
            if inspect.iscoroutinefunction(spec.handler):
                result = await spec.handler(ctx, admitted.args)
            else:
                result = await asyncio.to_thread(spec.handler, ctx, admitted.args)

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
            logger.exception("Error executing command '%s'", spec.name, extra={"zh": "執行指令 '%s' 時發生錯誤"})
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
            if device_id is not None and spec.takes_lock:
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

        invalid, admitted = self._admit(req)
        if invalid is not None:
            error, message = invalid
            return self._encode_envelope(started, req_id, device, cmd, "error", error, message, {})

        raw = await self._dispatch_command(req_id, admitted, reader=reader, writer=writer)
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
            error = str(raw.get("error") or "internal_error")
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
        spec = self._commands.get(cmd)
        data = {**(spec.default_data if spec is not None else {}), **payload}
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
        logger.info("Client connected: %s", peer, extra={"zh": "用戶端已連線: %s"})

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
            logger.exception("Unexpected error in client session with %s", peer,
                             extra={"zh": "與 %s 的用戶端工作階段發生未預期錯誤"})
        finally:
            self._active_writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client disconnected: %s", peer, extra={"zh": "用戶端已中斷: %s"})

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

        logger.info("TcpJsonServer listening on %s:%s", *self.server_address,
                    extra={"zh": "TcpJsonServer 監聽於 %s:%s"})

        # Wait until shutdown signal
        await self._shutdown_event.wait()

        logger.info("TcpJsonServer shutting down...", extra={"zh": "TcpJsonServer 關閉中..."})
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

        logger.info("TcpJsonServer stopped cleanly.", extra={"zh": "TcpJsonServer 已正常停止。"})

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
