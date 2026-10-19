"""Shared Core JSON protocol (version 1.0): envelope constants and the client framing.

One newline-delimited JSON object per request and per reply. Callers that own a
socket still decide when to connect, drop, or ignore a mismatched reply.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "1.0"

COMMON_FIELDS = frozenset({"protocol_version", "req_id", "device", "cmd"})
ENVELOPE_KEYS = frozenset({
    "protocol_version", "req_id", "device", "cmd", "status", "error", "message", "elapsed_sec",
})

_FLATTEN_OVERLAY = ("status", "error", "message", "elapsed_sec", "req_id", "device")


def build_request(
    cmd: str,
    req_id: str,
    device: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Envelope plus payload. Payload keys overwrite the envelope, including device."""
    req: Dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "req_id": req_id,
        "device": device,
        "cmd": cmd,
    }
    if payload:
        req.update(payload)
    return req


def encode_request(
    cmd: str,
    req_id: str,
    device: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> bytes:
    return dumps_request(build_request(cmd, req_id, device, payload))


def dumps_request(req: Dict[str, Any]) -> bytes:
    return (json.dumps(req) + "\n").encode("utf-8")


def read_frame(sock: Any, recv_size: int = 65536) -> bytes:
    """One newline-delimited frame. The peer closing first is an error, even with buffered bytes."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(recv_size)
        if not chunk:
            raise ConnectionError("Core Service closed connection")
        buf.extend(chunk)
    return bytes(buf.split(b"\n", 1)[0])


def read_reply(sock: Any, recv_size: int = 65536) -> Any:
    """Parse one reply frame. Does not check req_id."""
    return json.loads(read_frame(sock, recv_size).decode("utf-8"))


def req_id_matches(reply: Any, req_id: str) -> bool:
    return isinstance(reply, dict) and reply.get("req_id") == req_id


def flatten_reply(res: Dict[str, Any]) -> Dict[str, Any]:
    """Lift ``data`` to the top level. Envelope fields win over the same keys inside data."""
    if not isinstance(res.get("data"), dict):
        return res
    merged = dict(res["data"])
    for key in _FLATTEN_OVERLAY:
        if key in res:
            merged[key] = res[key]
    return merged
