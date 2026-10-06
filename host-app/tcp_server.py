"""Headless TCP JSON Protocol & Multiplexing Server.

Provides a standalone, headless TCP server that communicates with LabVIEW and
other clients using newline-delimited JSON. Supports concurrent multiplexed
client connections, req_id correlation, and graceful shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
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


class TcpJsonServer:
    """Headless TCP Server using newline-delimited JSON protocol."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        lock_manager: Optional[DeviceLockManager] = None,
        kvm_pool: Optional[KVMConnectionPool] = None,
        freeze_guard: Optional[FrameFreezeGuard] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.server_address: tuple[str, int] = (host, port)
        self.lock_manager = lock_manager if lock_manager is not None else DeviceLockManager()
        self.kvm_pool = kvm_pool if kvm_pool is not None else KVMConnectionPool()
        self.freeze_guard = freeze_guard if freeze_guard is not None else FrameFreezeGuard()

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

    async def _handle_ping(self, req: Dict[str, Any]) -> str:
        return "pong"

    async def _handle_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        uptime = time.time() - self._start_time if self._start_time else 0.0
        return {
            "status": "running",
            "uptime_seconds": round(uptime, 2),
            "active_connections": len(self._active_writers),
            "busy_devices": self.lock_manager.get_active_devices(),
            "kvm_pool": {
                "active_connections": self.kvm_pool.active_count(),
                "cached_ips": self.kvm_pool.get_active_ips(),
            },
            "freeze_guard": self.freeze_guard.get_status(),
            "version": "1.0.0",
        }

    async def _handle_check(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Station vision check handler with Frame Freeze Guard protection."""
        kvm = req.get("_kvm_client")
        station = str(req.get("station") or req.get("dev_type") or "BT").upper()
        device_id = str(req.get("device") or "default")
        threshold_window = req.get("freeze_threshold") or req.get("threshold_sec")

        if kvm is None:
            return {
                "status": "error",
                "error": "missing_kvm",
                "message": "KVM client not available. Specify 'kvm_ip' in request.",
            }

        # 1. Frame Freeze Guard verification across all stations (BT, FCT, DFU)
        is_live, freeze_err = await self.freeze_guard.verify_liveness(
            kvm,
            device_id=f"{station}:{device_id}",
            threshold_window=float(threshold_window) if threshold_window is not None else None,
        )
        if not is_live:
            err_dict = dict(freeze_err or {})
            err_dict["status"] = "error"
            if "error" not in err_dict:
                err_dict["error"] = "frame_frozen"
            err_dict["device"] = device_id
            return err_dict

        # 2. Station Vision Inspection for live frames
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
                    return {
                        "status": "error",
                        "error": "frame_frozen",
                        "message": "DFU round frame stream is frozen",
                    }
                return {
                    "status": "ok",
                    "station": station,
                    "kind": decision.kind,
                    "reason": decision.reason,
                    "results": decision.results,
                }
            except Exception as exc:
                logger.debug("DFU inspection fallback: %s", exc)

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
                    return {
                        "status": "error",
                        "error": "frame_frozen",
                        "message": r.get("message", "Frame presentation timestamp is static / frozen"),
                    }
                return {
                    "status": "ok",
                    "station": station,
                    "check_result": r,
                }
            except Exception as exc:
                logger.debug("Auto flow check exception: %s", exc)

        return {
            "status": "ok",
            "station": station,
            "verified": True,
            "message": "Live frame PTS verified",
        }

    async def _handle_shutdown(self, req: Dict[str, Any]) -> str:
        # Schedule the server loop to stop after replying
        if self._loop and self._shutdown_event:
            self._loop.call_soon(self._shutdown_event.set)
        return "shutting_down"

    def _generate_req_id(self) -> str:
        return f"req-{uuid.uuid4().hex[:8]}"

    async def _dispatch_command(
        self, req_id: str, cmd: str, req: Dict[str, Any]
    ) -> Dict[str, Any]:
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

        if device_id is not None:
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

        # Check KVM connection pooling if kvm_ip is provided
        raw_kvm_ip = req.get("kvm_ip")
        if raw_kvm_ip is not None and str(raw_kvm_ip).strip() != "":
            kvm_ip = str(raw_kvm_ip).strip()
            try:
                kvm_client = await self.kvm_pool.get_connection(kvm_ip)
                req["_kvm_client"] = kvm_client
            except Exception as exc:
                logger.exception("Failed to establish KVM connection to '%s' (req_id=%s)", kvm_ip, req_id)
                if device_id is not None:
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

            # If handler explicitly returns an envelope with custom status like busy/error/timeout/ok
            if isinstance(result, dict) and result.get("status") in ("error", "busy", "timeout"):
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
            if device_id is not None:
                self.lock_manager.release(device_id)

    async def _process_line(self, raw_line: bytes) -> bytes:
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
        resp = await self._dispatch_command(req_id, cmd.strip(), req)
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

                response_bytes = await self._process_line(line)
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
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    server = TcpJsonServer(host=args.host, port=args.port)
    print(f"Starting Headless TCP JSON Server on {args.host}:{args.port}...")
    server.serve_forever()


if __name__ == "__main__":
    main()
