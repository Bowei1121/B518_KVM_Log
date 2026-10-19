"""One Monitor UI connection to the Core Service (ADR-0006: one request at a time per socket).

Each Core command is routed to exactly one named connection. The UI owns the
Tk window; this module owns the sockets, so it can be tested without a display.
"""

from __future__ import annotations

import logging
import socket
import threading
import uuid
from typing import Any, Dict, Optional

from core_protocol import build_request, dumps_request, read_reply, req_id_matches

log = logging.getLogger(__name__)

# Which connection carries each Core command. Unknown commands have no route.
CHANNEL_BY_CMD: Dict[str, str] = {
    "ping": "status",
    "core_status": "status",
    "snapshot": "stream",
    "inspect": "inspect",
}


def channel_name_for(cmd: str) -> str:
    """Name of the connection that carries ``cmd``; raises ValueError for an unrouted command."""
    try:
        return CHANNEL_BY_CMD[cmd]
    except KeyError:
        raise ValueError(f"no Core connection is routed for command '{cmd}'") from None


def _hard_close(sock: Optional[socket.socket]) -> None:
    """shutdown + close. Call only from the thread blocked in request().

    abort() must not close. A timeout recv polls the fd; close() from the
    cancelling thread can drop that fd before poll runs again, and the waiter
    then sleeps until the socket timeout.
    """
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        sock.close()
    except Exception:
        pass


def client_error(req_id: str, error: str, message: str) -> Dict[str, Any]:
    return {"req_id": req_id, "status": "error", "error": error, "message": message}


class CoreChannel:
    """One Core socket; requests on it run strictly in order (ADR-0006).

    The lock serializes request/reply on this socket only. abort() never waits
    on that lock. It bumps a generation counter and shutdown()s the socket to
    wake a blocked recv. The request thread closes that socket. An idle socket
    (nobody holds the lock) is closed inside abort(); a request still queued
    on the lock keeps the generation it sampled and returns cancelled.
    """

    def __init__(self, name: str, host: str, port: int) -> None:
        self.name = name
        self.host = host
        self.port = int(port)
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._gen = 0
        # Detached by abort() so this thread does not drop the last reference
        # while a request is blocked in recv. The request thread closes them
        # under _lock. If the lock is free, abort() closes the idle fd itself.
        self._detached: list[socket.socket] = []

    def is_connected(self) -> bool:
        return self._sock is not None

    def set_target(self, host: str, port: int) -> None:
        if self.host != host or self.port != int(port):
            self.host = host
            self.port = int(port)
            self.abort()

    def abort(self) -> None:
        """Cancel requests that already sampled this generation. Never waits on the lock.

        shutdown() wakes a recv polling this fd. close() of that fd stays on
        the request thread, which holds the lock across the poll. When the
        lock is free there is no waiter, so the idle fd is closed here.
        """
        self._gen += 1
        sock = self._sock
        self._sock = None
        if sock is not None:
            self._detached.append(sock)
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                log.debug("abort shutdown failed errno=%s", exc.errno)
        # Idle only. blocking=False: a request inside recv keeps the lock and
        # closes the socket itself. Never block behind that request.
        if self._lock.acquire(blocking=False):
            try:
                self._reap_locked()
            finally:
                self._lock.release()

    def _reap_locked(self) -> None:
        """Close sockets abort() detached. Caller holds _lock."""
        detached = self._detached
        self._detached = []
        for sock in detached:
            self._drop_locked(sock)

    def connect(self, timeout: float = 2.0) -> bool:
        with self._lock:
            self._reap_locked()
            if self._sock is not None:
                return True
            try:
                s = socket.create_connection((self.host, self.port), timeout=timeout)
            except Exception:
                return False
            s.settimeout(timeout)
            self._sock = s
            return True

    def _drop_locked(self, sock: socket.socket) -> None:
        if self._sock is sock:
            self._sock = None
        _hard_close(sock)

    def request(self, cmd: str, payload: Optional[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
        req_id = f"ui-{uuid.uuid4().hex[:8]}"
        data = dumps_request(build_request(cmd, req_id, "", payload))
        # Sample before the lock so a request already queued behind an in-flight
        # one is cancelled, instead of reconnecting and sending after abort().
        gen = self._gen

        with self._lock:
            try:
                return self._request_locked(req_id, data, timeout, gen)
            finally:
                self._reap_locked()

    def _request_locked(
        self,
        req_id: str,
        data: bytes,
        timeout: float,
        gen: int,
    ) -> Dict[str, Any]:
        """Send one request. Caller holds _lock. gen was sampled before the lock."""
        self._reap_locked()
        sock = self._sock
        if sock is None:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=min(timeout, 2.0))
            except Exception as exc:
                return client_error(
                    req_id, "connection_failed",
                    f"Cannot connect to Core Service at {self.host}:{self.port}: {exc}",
                )
            # Publish before the generation check. abort() between the check and
            # the assignment used to see _sock is None and return without shutdown.
            self._attach_socket(sock)
            if gen != self._gen:
                self._drop_locked(sock)
                return client_error(req_id, "cancelled", "Request cancelled: Core target changed or UI closing")

        try:
            sock.settimeout(timeout)
            sock.sendall(data)
            reply = read_reply(sock)
        except Exception as exc:
            self._drop_locked(sock)
            if gen != self._gen:
                return client_error(req_id, "cancelled", "Request cancelled: Core target changed or UI closing")
            return client_error(req_id, "communication_error", f"Communication error with Core Service: {exc}")

        if not req_id_matches(reply, req_id):
            # Stale or foreign reply: never hand it to the UI; start clean on the next request.
            self._drop_locked(sock)
            return client_error(
                req_id, "req_id_mismatch",
                f"Reply req_id did not match request {req_id}; connection reset",
            )
        # Linux keeps bytes already in the receive buffer across SHUT_RDWR;
        # BSD/macOS drops them. Treat a reply that lost the race with abort as
        # cancelled on both, and let finally reap the detached socket.
        if gen != self._gen:
            return client_error(req_id, "cancelled", "Request cancelled: Core target changed or UI closing")
        return reply

    def _attach_socket(self, sock: socket.socket) -> None:
        """Make sock visible to abort() before the generation check."""
        self._sock = sock
