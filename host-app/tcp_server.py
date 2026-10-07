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
import uuid
from typing import Any, Callable, Coroutine, Dict, Optional, Set

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

logger = logging.getLogger("TcpJsonServer")


DEFAULT_STATIONS = [
    {"station": "DFU", "device": "1", "kvm_ip": "192.168.132.70"},
    {"station": "FCT", "device": "1", "kvm_ip": "192.168.132.71"},
    {"station": "BT", "device": "1", "kvm_ip": "192.168.132.72"},
]


class TcpJsonServer:
    """Headless TCP Server using newline-delimited JSON protocol."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        lock_manager: Optional[DeviceLockManager] = None,
        kvm_pool: Optional[KVMConnectionPool] = None,
        freeze_guard: Optional[FrameFreezeGuard] = None,
        stations_config: Optional[list[Dict[str, Any]]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.server_address: tuple[str, int] = (host, port)
        self.lock_manager = lock_manager if lock_manager is not None else DeviceLockManager()
        self.kvm_pool = kvm_pool if kvm_pool is not None else KVMConnectionPool()
        self.freeze_guard = freeze_guard if freeze_guard is not None else FrameFreezeGuard()
        self.stations_config: list[Dict[str, Any]] = (
            [dict(s) for s in stations_config]
            if stations_config is not None
            else [dict(s) for s in DEFAULT_STATIONS]
        )

        self._server: Optional[asyncio.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_event = threading.Event()
        self._shutdown_event: Optional[asyncio.Event] = None

        self._active_writers: Set[asyncio.StreamWriter] = set()
        self._handlers: Dict[str, Callable[..., Any]] = {}

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
    ) -> None:
        """Register a command handler.
        
        Handler can be either a synchronous callable or an async coroutine.
        It accepts the request dict and returns the data/response dict.
        """
        self._handlers[cmd] = handler

    def _register_default_handlers(self) -> None:
        self.register_handler("ping", self._handle_ping)
        self.register_handler("status", self._handle_status)
        self.register_handler("shutdown", self._handle_shutdown)
        self.register_handler("check", self._handle_check)
        self.register_handler("stations", self._handle_stations)
        self.register_handler("snapshot", self._handle_snapshot)

    async def _handle_ping(self, req: Dict[str, Any]) -> str:
        return "pong"

    async def _handle_stations(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Return real-time status of all configured test stations."""
        active_devices = set(self.lock_manager.get_active_devices())
        fg_status = self.freeze_guard.get_status()
        frozen_devices = set(fg_status.get("frozen_devices", []))
        active_ips = set(self.kvm_pool.get_active_ips())

        stations_list: list[Dict[str, Any]] = []
        for cfg in self.stations_config:
            st_name = str(cfg.get("station", "UNKNOWN")).upper()
            dev_id = str(cfg.get("device", "1"))
            kvm_ip = str(cfg.get("kvm_ip", ""))

            is_busy = dev_id in active_devices
            is_frozen = dev_id in frozen_devices or f"{st_name}:{dev_id}" in frozen_devices
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
        device_id = str(req.get("device") or "default")
        raw_kvm_ip = req.get("kvm_ip")
        kvm_ip = str(raw_kvm_ip).strip() if raw_kvm_ip else None

        if kvm is None:
            return {
                "status": "error",
                "error": "missing_kvm",
                "message": "KVM client not available. Specify 'kvm_ip' in request.",
                "device": device_id,
            }

        # Extract frame and PTS
        frame = None
        pts = None
        if hasattr(kvm, "latest_frame"):
            latest = kvm.latest_frame()
            if latest is not None and isinstance(latest, (tuple, list)) and len(latest) >= 5:
                frame = latest[0]
                pts = latest[4]
        if frame is None and hasattr(kvm, "frame"):
            frame = kvm.frame
        if pts is None and hasattr(kvm, "frame_presentation_time"):
            pts = kvm.frame_presentation_time

        if frame is None:
            return {
                "status": "error",
                "error": "no_frame",
                "message": "No frame received from KVM yet",
                "device": device_id,
            }

        # Support optional save_path
        save_path = req.get("save_path")
        saved_path_str: Optional[str] = None
        if save_path:
            p = Path(save_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                import cv2
                if hasattr(frame, "shape"):
                    cv2.imwrite(str(p), frame)
                    saved_path_str = str(p)
            except Exception as exc:
                logger.warning("Failed to save snapshot to %s: %s", p, exc)

        # Encode to JPEG/PNG base64
        fmt = str(req.get("format") or "jpeg").lower()
        quality = int(req.get("quality") or 80)
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
            "kvm_ip": kvm_ip,
            "device": device_id,
            "width": width,
            "height": height,
            "pts": pts,
            "format": fmt,
            "image_base64": img_b64,
            "saved_path": saved_path_str,
        }

    async def _evaluate_visual_condition(
        self,
        kvm: Any,
        station: str,
        device_id: str,
        req: Dict[str, Any],
        is_long_polling: bool = False,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Evaluate the live frame against station rules, patterns, or custom evaluators.
        
        Returns:
            (matched: bool, result_name: Optional[str], extra_data: Optional[Dict[str, Any]])
            If result_name == "ERROR_FROZEN", indicates a freeze error occurred during inspection.
        """
        # 1. Custom evaluator callback in request
        evaluator = req.get("evaluator")
        if callable(evaluator):
            try:
                res = evaluator(kvm)
                if inspect.iscoroutine(res):
                    res = await res
                if res is not None and res is not False:
                    match_res = "PASS" if res is True else str(res).upper()
                    return True, match_res, {}
                return False, None, {}
            except Exception as exc:
                logger.exception("Custom evaluator error: %s", exc)

        # 2. DFU Station
        if station == "DFU":
            try:
                from round_frame_consumer import RoundFrameGate, observe_latest_round_frame
                gate_key = f"{station}:{device_id}"
                if not hasattr(self, "_dfu_gates"):
                    self._dfu_gates: Dict[str, Any] = {}
                gate = self._dfu_gates.get(gate_key)
                if gate is None:
                    gate = RoundFrameGate(gate_key, require_presentation_time=True)
                    self._dfu_gates[gate_key] = gate
                decision = observe_latest_round_frame(kvm, gate)
                if decision.kind == "frozen" or decision.reason == "frame_frozen":
                    return False, "ERROR_FROZEN", {
                        "status": "error",
                        "error": "frame_frozen",
                        "message": "DFU round frame stream is frozen",
                        "device": device_id,
                    }
                if decision.kind in ("complete", "taken") or getattr(decision, "results_complete", False):
                    results = decision.results
                    if results and any(r[1] == "FAIL" for r in results):
                        final_res = "FAIL"
                    else:
                        final_res = "PASS"
                    return True, final_res, {
                        "kind": decision.kind,
                        "reason": decision.reason,
                        "results": results,
                    }
                # If not long polling and we observed something, return whatever state
                if not is_long_polling:
                    return True, "PASS", {
                        "kind": decision.kind,
                        "reason": decision.reason,
                        "results": decision.results,
                    }
                return False, None, {"kind": decision.kind, "reason": decision.reason}
            except Exception as exc:
                logger.debug("DFU inspection fallback: %s", exc)

        # 3. OpenCV Template Catalog Inspection (BT / FCT)
        template_root = req.get("template_root")
        if template_root:
            try:
                from auto_flow import run_check
                r = await run_check(
                    kvm,
                    device=station,
                    template_root=template_root,
                    freeze_guard=self.freeze_guard,
                )
                if r.get("error") == "frame_frozen":
                    return False, "ERROR_FROZEN", {
                        "status": "error",
                        "error": "frame_frozen",
                        "message": r.get("message", "Frame presentation timestamp is static / frozen"),
                        "device": device_id,
                    }
                if r.get("ok"):
                    if r.get("testing"):
                        if not is_long_polling:
                            return True, "TESTING", {"check_result": r}
                        return False, None, {"check_result": r}
                    rows = r.get("rows", [])
                    if rows and any(row[1] == "fail" for row in rows):
                        final_res = "FAIL"
                    else:
                        final_res = "PASS"
                    return True, final_res, {"check_result": r}
                if not is_long_polling:
                    return False, None, {"check_result": r}
                return False, None, {"check_result": r}
            except Exception as exc:
                logger.debug("Auto flow check exception: %s", exc)

        # 4. Target Pattern matching
        target_pattern = req.get("target_pattern") or req.get("pattern")
        if target_pattern is not None:
            target_str = str(target_pattern).upper()
            for attr in ("result", "visual_state", "pattern", "status", "visual_result"):
                val = getattr(kvm, attr, None)
                if val is not None and str(val).upper() == target_str:
                    return True, target_str, {}
            frame_val = getattr(kvm, "frame", None)
            if isinstance(frame_val, str) and frame_val.upper() == target_str:
                return True, target_str, {}
            return False, None, {}

        # 5. Direct KVM visual state attributes
        if hasattr(kvm, "result") and kvm.result is not None:
            res_str = str(kvm.result).upper()
            if res_str in ("PASS", "FAIL"):
                return True, res_str, {}
        if hasattr(kvm, "visual_state") and kvm.visual_state is not None:
            vstate = str(kvm.visual_state).upper()
            if vstate in ("PASS", "FAIL", "COMPLETE", "DONE"):
                res_str = "PASS" if vstate in ("PASS", "COMPLETE", "DONE") else "FAIL"
                return True, res_str, {}
            if vstate in ("TESTING", "BUSY", "WAITING", "RUNNING", "MONITORING"):
                return False, None, {}

        # 6. Default fallback: if not long polling, stream PTS liveness verification is sufficient
        if not is_long_polling:
            return True, "PASS", {"verified": True, "message": "Live frame PTS verified"}

        return False, None, {}

    async def _handle_check(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Station vision check handler with long-polling and Frame Freeze Guard protection."""
        kvm = req.get("_kvm_client")
        station = str(req.get("station") or req.get("dev_type") or "BT").upper()
        device_id = str(req.get("device") or "default")
        threshold_window = req.get("freeze_threshold") or req.get("threshold_sec")
        guard_threshold = float(threshold_window) if threshold_window is not None else None
        dev_key = f"{station}:{device_id}"

        if kvm is None:
            return {
                "status": "error",
                "error": "missing_kvm",
                "message": "KVM client not available. Specify 'kvm_ip' in request.",
                "device": device_id,
            }

        raw_timeout = req.get("timeout_sec") if req.get("timeout_sec") is not None else req.get("timeout")
        has_timeout_arg = raw_timeout is not None
        timeout_val: float = 0.0
        if has_timeout_arg:
            try:
                timeout_val = float(raw_timeout)
            except (ValueError, TypeError):
                return {
                    "status": "error",
                    "error": "invalid_timeout",
                    "message": f"Invalid timeout_sec: {raw_timeout}",
                    "device": device_id,
                }
            if timeout_val < 0:
                return {
                    "status": "error",
                    "error": "invalid_timeout",
                    "message": f"Negative timeout_sec: {timeout_val}",
                    "device": device_id,
                }

        is_long_polling = has_timeout_arg and timeout_val > 0.0
        poll_interval = float(req.get("poll_interval") or 0.1)

        start_time = time.monotonic()
        reader = req.get("_reader")
        writer = req.get("_writer")

        while True:
            # 0. Check client connection liveness & server status
            is_client_gone = (reader is not None and reader.at_eof()) or (
                writer is not None and (writer.is_closing() or getattr(writer, "_transport", None) is None)
            )
            if is_client_gone:
                logger.info("Client disconnected during check for device %s", device_id)
                return {
                    "status": "cancelled",
                    "error": "client_disconnected",
                    "device": device_id,
                }
            if not self._is_running or (self._shutdown_event and self._shutdown_event.is_set()):
                return {
                    "status": "error",
                    "error": "server_shutting_down",
                    "device": device_id,
                }

            # 1. Frame Freeze Guard verification across all stations (BT, FCT, DFU)
            is_live, freeze_err = await self.freeze_guard.verify_liveness(
                kvm,
                device_id=dev_key,
                threshold_window=guard_threshold,
                poll_interval=min(poll_interval, 0.05),
            )
            if not is_live:
                elapsed = time.monotonic() - start_time
                err_dict = dict(freeze_err or {})
                err_dict["status"] = "error"
                if "error" not in err_dict:
                    err_dict["error"] = "frame_frozen"
                err_dict["device"] = device_id
                err_dict["elapsed_sec"] = round(elapsed, 2)
                return err_dict

            # 2. Visual condition evaluation
            matched, res_name, extra_data = await self._evaluate_visual_condition(
                kvm, station, device_id, req, is_long_polling=is_long_polling
            )

            if res_name == "ERROR_FROZEN":
                elapsed = time.monotonic() - start_time
                err_resp = dict(extra_data or {})
                err_resp["elapsed_sec"] = round(elapsed, 2)
                return err_resp

            if matched:
                elapsed = time.monotonic() - start_time
                resp = {
                    "status": "ok",
                    "result": res_name or "PASS",
                    "elapsed_sec": round(elapsed, 2),
                    "station": station,
                    "device": device_id,
                }
                if extra_data:
                    resp.update(extra_data)
                return resp

            # 3. Timeout check
            now = time.monotonic()
            elapsed = now - start_time
            if has_timeout_arg and elapsed >= timeout_val:
                return {
                    "status": "timeout",
                    "result": "NONE",
                    "elapsed_sec": round(max(elapsed, timeout_val), 1),
                    "station": station,
                    "device": device_id,
                }
            elif not has_timeout_arg:
                return {
                    "status": "ok",
                    "result": "PASS",
                    "verified": True,
                    "elapsed_sec": round(elapsed, 2),
                    "station": station,
                    "device": device_id,
                    "message": "Live frame PTS verified",
                }

            # Wait before next poll iteration
            sleep_time = min(poll_interval, max(0.01, timeout_val - elapsed))
            await asyncio.sleep(sleep_time)

    async def _handle_shutdown(self, req: Dict[str, Any]) -> str:
        # Schedule the server loop to stop after replying cleanly to the client
        if self._loop and self._shutdown_event:
            self._loop.call_later(0.05, self._shutdown_event.set)
        return "shutting_down"

    def _generate_req_id(self) -> str:
        return f"req-{uuid.uuid4().hex[:8]}"

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

        handler = self._handlers.get(cmd)
        if not handler:
            return {
                "req_id": req_id,
                "status": "error",
                "error": "unknown_command",
                "message": f"Unknown command: '{cmd}'",
            }

        # Check per-device locking
        raw_device = req.get("device")
        device_id: Optional[str] = (
            str(raw_device).strip()
            if raw_device is not None and str(raw_device).strip() != ""
            else None
        )

        is_read_only = (
            cmd in ("snapshot", "frame")
            or req.get("no_lock") is True
            or req.get("read_only") is True
        )

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

        # Check KVM connection pooling if kvm_ip is provided or inferred for snapshot
        raw_kvm_ip = req.get("kvm_ip")
        if cmd in ("snapshot", "frame") and (raw_kvm_ip is None or not str(raw_kvm_ip).strip()) and (req.get("station") or req.get("device")):
            st = str(req.get("station") or "").upper()
            dev = str(req.get("device") or "")
            for cfg in getattr(self, "stations_config", []):
                if (st and cfg.get("station") == st) or (dev and str(cfg.get("device")) == dev):
                    raw_kvm_ip = cfg.get("kvm_ip")
                    req["kvm_ip"] = raw_kvm_ip
                    break

        if raw_kvm_ip is not None and str(raw_kvm_ip).strip() != "":
            kvm_ip = str(raw_kvm_ip).strip()
            try:
                kvm_client = await self.kvm_pool.get_connection(kvm_ip)
                req["_kvm_client"] = kvm_client
            except Exception as exc:
                logger.exception("Failed to establish KVM connection to '%s' (req_id=%s)", kvm_ip, req_id)
                if device_id is not None and not is_read_only:
                    self.lock_manager.release(device_id)
                err_resp = {
                    "req_id": req_id,
                    "status": "error",
                    "error": "kvm_connection_failed",
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
            if isinstance(result, dict) and result.get("status") in ("error", "busy", "timeout", "cancelled"):
                resp = dict(result)
                if "req_id" not in resp:
                    resp["req_id"] = req_id
                if "device" not in resp and device_id is not None:
                    resp["device"] = device_id
                return resp

            if isinstance(result, dict) and result.get("status") == "ok":
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

    async def _process_line(
        self,
        raw_line: bytes,
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
    ) -> bytes:
        """Parse one line, dispatch command, and return newline-terminated JSON response."""
        line_str = raw_line.decode("utf-8", errors="replace").strip()
        if not line_str:
            return b""

        # 1. Parse JSON
        try:
            req = json.loads(line_str)
        except Exception as exc:
            err_resp = {
                "req_id": None,
                "status": "error",
                "error": "invalid_json",
                "message": f"Failed to parse JSON: {exc}",
            }
            return (json.dumps(err_resp) + "\n").encode("utf-8")

        # 2. Validate object type
        if not isinstance(req, dict):
            err_resp = {
                "req_id": None,
                "status": "error",
                "error": "invalid_request",
                "message": "Request must be a JSON object",
            }
            return (json.dumps(err_resp) + "\n").encode("utf-8")

        # 3. Extract or assign req_id
        req_id = req.get("req_id")
        if req_id is None or str(req_id).strip() == "":
            req_id = self._generate_req_id()

        # 4. Validate cmd
        cmd = req.get("cmd")
        if not cmd or not isinstance(cmd, str):
            err_resp = {
                "req_id": req_id,
                "status": "error",
                "error": "missing_command",
                "message": "Missing 'cmd' field",
            }
            return (json.dumps(err_resp) + "\n").encode("utf-8")

        # 5. Dispatch command
        resp = await self._dispatch_command(req_id, cmd.strip(), req, reader=reader, writer=writer)
        return (json.dumps(resp) + "\n").encode("utf-8")

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless TCP JSON Server for B518 JetKVM Relay")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
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

    server = TcpJsonServer(host=args.host, port=args.port)
    print(f"Starting Headless TCP JSON Server on {args.host}:{args.port}...")
    server.serve_forever()


if __name__ == "__main__":
    main()
