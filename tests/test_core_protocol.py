"""External behavior of the shared Core protocol client."""

from __future__ import annotations

import json
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


def test_read_reply_one_line_split_across_recvs():
    raw = b'{"req_id": "split", "status": "ok"}\n'
    sock = ChunkSocket([raw[:8], raw[8:15], raw[15:]])
    assert read_reply(sock)["req_id"] == "split"


def test_read_reply_peer_closes_on_partial_line():
    sock = ChunkSocket([b'{"status": "ok"'])
    with pytest.raises(ConnectionError):
        read_reply(sock)


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
    assert flatten_reply(unchanged) is unchanged or flatten_reply(unchanged) == unchanged
