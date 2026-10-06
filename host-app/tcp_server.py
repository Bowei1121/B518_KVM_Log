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

logger = logging.getLogger("TcpJsonServer")


class TcpJsonServer:
    """Headless TCP Server using newline-delimited JSON protocol."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000) -> None:
        self.host = host
        self.port = port
        self.server_address: tuple[str, int] = (host, port)

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

    async def _handle_ping(self, req: Dict[str, Any]) -> str:
        return "pong"

    async def _handle_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        uptime = time.time() - self._start_time if self._start_time else 0.0
        return {
            "status": "running",
            "uptime_seconds": round(uptime, 2),
            "active_connections": len(self._active_writers),
            "version": "1.0.0",
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

        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(req)
            else:
                result = handler(req)

            # If handler explicitly returns an envelope with custom status like busy/error
            if isinstance(result, dict) and result.get("status") in ("error", "busy"):
                resp = dict(result)
                if "req_id" not in resp:
                    resp["req_id"] = req_id
                return resp

            return {
                "req_id": req_id,
                "status": "ok",
                "data": result,
            }
        except Exception as exc:
            logger.exception("Error executing command '%s'", cmd)
            return {
                "req_id": req_id,
                "status": "error",
                "error": "internal_error",
                "message": str(exc),
            }

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
        self._start_event.set()

        logger.info("TcpJsonServer listening on %s:%s", *self.server_address)

        # Wait until shutdown signal
        await self._shutdown_event.wait()

        logger.info("TcpJsonServer shutting down...")
        self._is_running = False

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
