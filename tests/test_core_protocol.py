"""External behavior of the shared Core protocol client."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    build_request,
    encode_request,
    flatten_reply,
    read_reply,
    req_id_matches,
)
from protocol_helpers import recv_reply  # noqa: E402


class ChunkSocket:
    """Returns scripted recv chunks, then b'' (peer closed)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, _n):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_build_request_uses_shared_version_and_merges_payload():
    req = build_request("snapshot", "ui-1", device="DFU-01", payload={"format": "jpeg", "device": "BT-01"})
    assert req["protocol_version"] == PROTOCOL_VERSION == "1.0"
    assert req["req_id"] == "ui-1"
    assert req["cmd"] == "snapshot"
    assert req["format"] == "jpeg"
    assert req["device"] == "BT-01"
    wire = encode_request("ping", "p1")
    assert wire.endswith(b"\n")
    assert json.loads(wire.decode()) == {
        "protocol_version": "1.0",
        "req_id": "p1",
        "device": "",
        "cmd": "ping",
    }


def test_read_reply_one_recv_holds_several_lines_and_keeps_the_first():
    body = {"req_id": "a", "status": "ok", "data": {"uptime_seconds": 3}}
    extra = {"req_id": "b", "status": "ok"}
    raw = (json.dumps(body) + "\n" + json.dumps(extra) + "\n").encode()
    reply = read_reply(ChunkSocket([raw]))
    assert reply == body


def test_read_reply_without_buf_never_carries_bytes_to_another_socket():
    """Leftover bytes die with the call, so a new socket (even at a reused address) starts clean."""
    for _ in range(50):
        old = ChunkSocket([b'{"req_id": "old"}\n{"req_id": "leftover"}\n'])
        assert read_reply(old)["req_id"] == "old"
        del old
        fresh = ChunkSocket([b'{"req_id": "new"}\n'])
        assert read_reply(fresh)["req_id"] == "new"


def test_read_reply_with_buf_returns_following_frames_in_order():
    sock = ChunkSocket([b'{"req_id": "a"}\n{"req_id": "b"}\n{"req_', b'id": "c"}\n'])
    buf = bytearray()
    assert [read_reply(sock, buf=buf)["req_id"] for _ in range(3)] == ["a", "b", "c"]
    assert buf == bytearray()


def test_recv_reply_keeps_frames_per_connection_only():
    left, right = socket.socketpair()
    try:
        right.sendall(b'{"req_id": "1"}\n{"req_id": "2"}\n')
        assert recv_reply(left)["req_id"] == "1"
        assert recv_reply(left)["req_id"] == "2"
        right.sendall(b'{"req_id": "3"}\n{"req_id": "stale"}\n')
        assert recv_reply(left)["req_id"] == "3"
    finally:
        left.close()
        right.close()
    del left, right
    for _ in range(20):
        left, right = socket.socketpair()
        try:
            right.sendall(b'{"req_id": "new"}\n')
            assert recv_reply(left)["req_id"] == "new"
        finally:
            left.close()
            right.close()


def test_read_reply_one_line_split_across_recvs():
    raw = b'{"req_id": "split", "status": "ok"}\n'
    sock = ChunkSocket([raw[:8], raw[8:15], raw[15:]])
    assert read_reply(sock)["req_id"] == "split"


def test_read_reply_peer_closes_on_partial_line():
    sock = ChunkSocket([b'{"status": "ok"'])
    with pytest.raises(ConnectionError):
        read_reply(sock)


def test_read_reply_empty_line_is_connection_error():
    with pytest.raises(ConnectionError):
        read_reply(ChunkSocket([b"\n"]))


def test_req_id_mismatch():
    assert req_id_matches({"req_id": "ui-1", "status": "ok"}, "ui-1")
    assert not req_id_matches({"req_id": "other"}, "ui-1")
    assert not req_id_matches(["not-a-dict"], "ui-1")


def test_flatten_reply_lifts_data_and_keeps_envelope_status():
    flat = flatten_reply({
        "req_id": "ui-9",
        "status": "ok",
        "error": "",
        "message": "",
        "elapsed_sec": 0.2,
        "device": "DFU-01",
        "data": {"uptime_seconds": 4, "status": "ignored-inner", "stations": []},
    })
    assert flat["status"] == "ok"
    assert flat["uptime_seconds"] == 4
    assert flat["req_id"] == "ui-9"
    assert flat["elapsed_sec"] == 0.2
    assert flat["device"] == "DFU-01"
    assert flat["stations"] == []
    assert "data" not in flat
    unchanged = {"status": "error", "error": "connection_failed", "message": "down"}
    assert flatten_reply(unchanged) is unchanged


def test_flatten_reply_error_with_data_keeps_envelope_error():
    flat = flatten_reply({
        "req_id": "ui-err",
        "status": "error",
        "error": "device_busy",
        "message": "busy",
        "elapsed_sec": 1.5,
        "device": "BT-01",
        "data": {"status": "running", "stations": [{"device": "BT-01"}]},
    })
    assert flat["status"] == "error"
    assert flat["error"] == "device_busy"
    assert flat["message"] == "busy"
    assert flat["stations"] == [{"device": "BT-01"}]
    assert "data" not in flat
