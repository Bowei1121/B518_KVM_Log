"""Monitor UI client sends protocol 1.0 requests addressed by device ID only (ticket #9).

A recording TCP stub stands in for Core Service, so these tests see exactly what the UI sends.
"""

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request, read_reply  # noqa: E402

from ui_app import CoreServiceClient  # noqa: E402

FORBIDDEN = {"kvm_ip", "station", "save_path"}


class RecordingCore:
    """Accepts one request per connection, records it, and answers with a canned 1.0 reply."""

    def __init__(self, reply_for):
        self.reply_for = reply_for
        self.requests = []
        self._sock = socket.create_server(("127.0.0.1", 0))
        self.address = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    request = read_reply(conn)
                except ConnectionError:
                    continue
                self.requests.append(request)
                reply = self.reply_for(request)
                conn.sendall(dumps_request(reply))

    def close(self):
        self._sock.close()


@pytest.fixture
def core():
    def reply_for(request):
        base = {"protocol_version": "1.0", "req_id": request["req_id"], "device": request.get("device", ""),
                "cmd": request["cmd"], "status": "ok", "error": "", "message": "", "elapsed_sec": 0.0}
        if request["cmd"] == "snapshot":
            data = {"width": 4, "height": 3, "format": "jpeg", "image_base64": "AAAA"}
        elif request["cmd"] in ("check", "inspect"):
            data = {"kind": "taken", "state": "complete", "reason": "reliable_complete", "capacity": 4,
                    "results": [{"slot": 1, "status": "PASS"}]}
        elif request["cmd"] == "core_status":
            data = {"status": "running", "busy_devices": [], "stations": [
                {"station": "DFU", "device": "DFU-01", "status": "IDLE", "connected": True, "channels": 4}]}
        else:
            data = {}
        return {**base, "data": data}

    stub = RecordingCore(reply_for)
    try:
        yield stub
    finally:
        stub.close()


def _client(core):
    host, port = core.address
    return CoreServiceClient(host=host, port=port)


def test_snapshot_request_is_addressed_by_device_id_only(core):
    client = _client(core)

    res = client.get_snapshot(device="DFU-01", quality=65, timeout=3.0)

    request = core.requests[-1]
    assert request["protocol_version"] == "1.0"
    assert request["cmd"] == "snapshot"
    assert request["device"] == "DFU-01"
    assert FORBIDDEN.isdisjoint(request)
    assert res["status"] == "ok"
    assert res["image_base64"] == "AAAA"
    client.disconnect()


def test_manual_check_request_carries_device_and_timeout_only(core):
    client = _client(core)

    res = client.manual_check(device="DFU-01", timeout_sec=2.0)

    request = core.requests[-1]
    assert request["cmd"] == "inspect"
    assert request["device"] == "DFU-01"
    assert request["timeout_sec"] == 2.0
    assert FORBIDDEN.isdisjoint(request)
    assert res["kind"] == "taken"
    assert res["results"] == [{"slot": 1, "status": "PASS"}]
    client.disconnect()


def test_status_is_read_from_core_status_with_device_list(core):
    client = _client(core)

    res = client.get_status(timeout=2.0)

    assert core.requests[-1]["cmd"] == "core_status"
    assert [st["device"] for st in res["stations"]] == ["DFU-01"]
    assert "kvm_ip" not in json.dumps(core.requests)
    client.disconnect()
