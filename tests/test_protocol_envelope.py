"""Socket-level tests for the protocol 1.0 envelope and request validation (ticket #3).

Every reply carries the same fixed envelope fields; every validation error code
from docs/PROTOCOL.md section 6 is reachable over the wire.
"""

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request  # noqa: E402
from protocol_helpers import recv_reply  # noqa: E402

from protocol_helpers import make_registry  # noqa: E402
from tcp_server import CORE_VERSION, CommandSpec, TcpJsonServer  # noqa: E402

ENVELOPE_FIELDS = {
    "protocol_version", "req_id", "device", "cmd", "status", "error", "message", "elapsed_sec", "data",
}


@pytest.fixture
def server():
    srv = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0)
    srv.start()
    time.sleep(0.05)
    try:
        yield srv
    finally:
        srv.stop()


def _exchange(srv, raw: bytes) -> dict:
    host, port = srv.server_address
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.sendall(raw)
        return recv_reply(sock)


def _send(srv, obj: dict) -> dict:
    return _exchange(srv, dumps_request(obj))


def _assert_envelope(resp: dict) -> None:
    assert set(resp.keys()) == ENVELOPE_FIELDS
    assert resp["protocol_version"] == "1.0"
    assert isinstance(resp["data"], dict)
    assert isinstance(resp["elapsed_sec"], (int, float))
    # 1.0 forbids null anywhere in the reply
    assert all(v is not None for v in _walk(resp))


def _walk(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
        yield value
    else:
        yield value


def test_invalid_json_echoes_empty_envelope(server):
    resp = _exchange(server, b"NOT_JSON\n")

    _assert_envelope(resp)
    assert (resp["req_id"], resp["device"], resp["cmd"]) == ("", "", "")
    assert resp["status"] == "error"
    assert resp["error"] == "invalid_json"


def test_non_object_is_invalid_request(server):
    resp = _exchange(server, b'["ping"]\n')

    _assert_envelope(resp)
    assert resp["error"] == "invalid_request"


def test_missing_protocol_version_is_unsupported(server):
    resp = _send(server, {"req_id": "r1", "device": "", "cmd": "ping"})

    _assert_envelope(resp)
    assert resp["req_id"] == "r1"
    assert resp["error"] == "unsupported_protocol_version"


def test_wrong_protocol_version_is_unsupported(server):
    resp = _send(server, {"protocol_version": "2.0", "req_id": "r2", "device": "", "cmd": "ping"})

    assert resp["error"] == "unsupported_protocol_version"


@pytest.mark.parametrize("req_id", [None, ""])
def test_missing_req_id_is_rejected_not_generated(server, req_id):
    payload = {"protocol_version": "1.0", "device": "", "cmd": "ping"}
    if req_id is not None:
        payload["req_id"] = req_id

    resp = _send(server, payload)

    _assert_envelope(resp)
    assert resp["error"] == "missing_req_id"
    assert resp["req_id"] == (req_id or "")


def test_missing_cmd_is_rejected(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r3", "device": ""})

    assert resp["error"] == "missing_command"
    assert resp["cmd"] == ""


def test_unknown_command_is_rejected(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r4", "device": "", "cmd": "nope"})

    assert resp["error"] == "unknown_command"
    assert resp["cmd"] == "nope"


def test_unknown_field_is_rejected(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r5", "device": "", "cmd": "ping", "typo": 1})

    assert resp["error"] == "unknown_field"


@pytest.mark.parametrize("field", ["kvm_ip", "station"])
def test_forbidden_field_is_rejected_for_device_commands(server, field):
    resp = _send(server, {
        "protocol_version": "1.0", "req_id": "r6", "device": "DFU-01", "cmd": "snapshot",
        "format": "jpeg", "quality": 80, field: "192.168.200.1",
    })

    assert resp["error"] == "forbidden_field"


def test_missing_device_is_rejected_for_snapshot(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r7", "device": "", "cmd": "snapshot",
                          "format": "jpeg", "quality": 80})

    assert resp["error"] == "missing_device"


def test_unknown_device_is_rejected(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r8", "device": "dfu-01", "cmd": "snapshot",
                          "format": "jpeg", "quality": 80})

    assert resp["error"] == "unknown_device"
    assert resp["device"] == "dfu-01"


def test_ping_with_device_is_invalid_argument(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "r9", "device": "DFU-01", "cmd": "ping"})

    assert resp["error"] == "invalid_argument"


def test_ping_returns_pong_and_core_version(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "p1", "device": "", "cmd": "ping"})

    _assert_envelope(resp)
    assert resp["status"] == "ok"
    assert resp["error"] == ""
    assert resp["message"] == ""
    assert resp["data"] == {"pong": True, "core_version": resp["data"]["core_version"]}
    assert resp["data"]["core_version"] != ""


def test_status_lists_devices_in_order_without_kvm_ip(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "s1", "device": "", "cmd": "status"})

    _assert_envelope(resp)
    assert resp["status"] == "ok"
    assert isinstance(resp["data"]["core_uptime_sec"], int)
    devices = resp["data"]["devices"]
    assert [d["device"] for d in devices if d["station"] == "DFU"] == [f"DFU-{i:02d}" for i in range(1, 21)]
    assert set(devices[0].keys()) == {"device", "station", "connected", "busy", "frozen", "channels"}
    assert devices[0]["station"] == "DFU"
    assert devices[0]["channels"] == 4
    assert all("kvm_ip" not in d for d in devices)
    assert "kvm_ip" not in json.dumps(resp)


def test_core_status_keeps_internal_state_for_admin_tools(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "c1", "device": "", "cmd": "core_status"})

    assert resp["status"] == "ok"
    assert resp["data"]["status"] == "running"
    assert "kvm_pool" in resp["data"]


def test_snapshot_error_reply_has_default_data_fields(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "sn1", "device": "DFU-01", "cmd": "snapshot",
                          "format": "jpeg", "quality": 80, "kvm_ip": "1.2.3.4"})

    _assert_envelope(resp)
    assert resp["error"] == "forbidden_field"
    assert resp["data"] == {"width": 0, "height": 0, "format": "", "image_base64": ""}


def test_ok_reply_for_status_error_message_are_empty(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "ok1", "device": "", "cmd": "status"})

    assert resp["error"] == ""
    assert resp["message"] == ""


def test_reply_never_auto_assigns_req_id_when_request_has_one(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "keep-me", "device": "", "cmd": "ping"})

    assert resp["req_id"] == "keep-me"
    assert resp["device"] == ""
    assert resp["cmd"] == "ping"


@pytest.mark.parametrize("req_id", [5, True, ["r"], {"a": 1}])
def test_present_non_string_req_id_is_invalid_request(server, req_id):
    resp = _send(server, {"protocol_version": "1.0", "req_id": req_id, "device": "", "cmd": "ping"})

    _assert_envelope(resp)
    assert resp["error"] == "invalid_request"
    assert resp["req_id"] == ""


@pytest.mark.parametrize("cmd", ["check_finish", "inspect"])
def test_check_error_reply_uses_unknown_kind(server, cmd):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "k1", "device": "dfu-01", "cmd": cmd,
                          "timeout_sec": 0})

    assert resp["error"] == "unknown_device"
    assert resp["data"] == {
        "kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": [],
    }


@pytest.mark.parametrize("items", [
    [],
    "nope",
    [{"slot": 1}],
    [{"slot": True, "sn": "A"}],
    [{"slot": 1, "sn": "A"}, {"slot": 1, "sn": "B"}],
    [{"slot": 1, "sn": ""}],
    [{"slot": 1, "sn": "S" * 65}],
    [{"slot": 1, "sn": "A", "extra": 1}],
])
def test_input_items_are_refused_before_lock_and_kvm_connect(server, items):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "in-bad", "device": "DFU-01", "cmd": "input",
                          "items": items})

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert not server.lock_manager.is_locked("DFU-01")
    assert server.kvm_pool.active_count() == 0


def test_input_slot_outside_the_channel_count_is_refused_without_kvm(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "in9", "device": "DFU-01", "cmd": "input",
                          "items": [{"slot": 9, "sn": "SN9"}]})

    assert resp["error"] == "invalid_argument"
    assert not server.lock_manager.is_locked("DFU-01")
    assert server.kvm_pool.active_count() == 0


@pytest.mark.parametrize("payload", [
    {"format": "gif", "quality": 80},
    {"format": "jpeg"},
    {"format": "jpeg", "quality": True},
    {"format": "jpeg", "quality": 0},
    {"format": "jpeg", "quality": 101},
    {"format": "png", "quality": 5},
])
def test_snapshot_format_and_quality_are_refused_before_kvm_connect(server, payload):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "sn-bad", "device": "DFU-01", "cmd": "snapshot",
                          **payload})

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert not server.lock_manager.is_locked("DFU-01")
    assert server.kvm_pool.active_count() == 0


@pytest.mark.parametrize("payload", [
    {"format": "jpeg", "quality": 1},
    {"format": "jpeg", "quality": 80},
    {"format": "png", "quality": 0},
])
def test_valid_snapshot_formats_pass_validation(server, payload):
    req = {"protocol_version": "1.0", "req_id": "sn-ok", "device": "DFU-01", "cmd": "snapshot", **payload}

    assert server._validate_request(req) is None


# One row per protocol command: (cmd, device_bound, takes_lock). Expected data is written out here, not read
# from the server, so a dropped or changed default_data fails the test (main's DEFAULT_DATA values).
_UNKNOWN_DATA = {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []}
COMMAND_ROWS = [
    ("ping", False, True), ("status", False, True), ("core_status", False, True), ("shutdown", False, True),
    ("snapshot", True, False), ("check_start", True, True), ("check_finish", True, True),
    ("inspect", True, False), ("input", True, True), ("start_test", True, True), ("monitor_start", True, True),
]
EXPECTED_DEFAULT_DATA = {
    "ping": {"pong": False, "core_version": ""},
    "status": {"core_uptime_sec": 0, "devices": []},
    "core_status": {},
    "shutdown": {},
    "snapshot": {"width": 0, "height": 0, "format": "", "image_base64": ""},
    "check_start": _UNKNOWN_DATA,
    "check_finish": _UNKNOWN_DATA,
    "inspect": _UNKNOWN_DATA,
    "input": {"entered": []},
    "start_test": {"action": ""},
    "monitor_start": {"state": "", "capacity": 0},
}


def test_command_table_has_exactly_the_protocol_commands(server):
    assert set(server._commands) == {cmd for cmd, _, _ in COMMAND_ROWS} == set(EXPECTED_DEFAULT_DATA)


@pytest.mark.parametrize("cmd, device_bound, takes_lock", COMMAND_ROWS)
def test_command_spec_device_binding_and_lock(server, cmd, device_bound, takes_lock):
    assert server._commands[cmd].device_bound is device_bound
    assert server._commands[cmd].takes_lock is takes_lock


@pytest.mark.parametrize("cmd, device_bound, takes_lock", COMMAND_ROWS)
def test_device_field_is_checked_against_the_spec(server, cmd, device_bound, takes_lock):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "dv", "device": "" if device_bound else "DFU-01",
                          "cmd": cmd})

    _assert_envelope(resp)
    assert resp["error"] == ("missing_device" if device_bound else "invalid_argument")


@pytest.mark.parametrize("cmd, device_bound, takes_lock", COMMAND_ROWS)
def test_error_reply_data_matches_main_default_data(server, cmd, device_bound, takes_lock):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "dd", "device": "DFU-01" if device_bound else "",
                          "cmd": cmd, "typo": 1})

    _assert_envelope(resp)
    assert resp["error"] == "unknown_field"
    assert resp["data"] == EXPECTED_DEFAULT_DATA[cmd]


@pytest.mark.parametrize("cmd, device", [("ping", "DFU-01"), ("status", "DFU-01")])
def test_error_on_no_device_command_keeps_default_data(server, cmd, device):
    """The reviewer's case: a no-device command refused for a stray device still reports its fixed data."""
    resp = _send(server, {"protocol_version": "1.0", "req_id": "nd", "device": device, "cmd": cmd})

    assert resp["error"] == "invalid_argument"
    assert resp["data"] == EXPECTED_DEFAULT_DATA[cmd]


@pytest.mark.parametrize("cmd", ["snapshot", "check_start", "check_finish", "inspect", "input", "monitor_start"])
def test_missing_required_field_is_refused_before_lock_and_kvm(server, cmd):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "rf", "device": "DFU-01", "cmd": cmd})

    assert resp["status"] == "error"
    assert resp["error"] == "invalid_argument"
    assert not server.lock_manager.is_locked("DFU-01")
    assert server.kvm_pool.active_count() == 0


def test_core_status_reports_core_version(server):
    resp = _send(server, {"protocol_version": "1.0", "req_id": "cv", "device": "", "cmd": "core_status"})

    assert resp["data"]["core_version"] == CORE_VERSION
    assert "version" not in resp["data"]


def test_one_registered_spec_drives_validation_dispatch_and_envelope(server):
    """A new command is one CommandSpec: its fields, parse, handler and default data all come from it."""
    from hid_session import FlowError

    def parse_text(req, entry):
        if not isinstance(req.get("text"), str):
            raise FlowError("invalid_argument", "text must be a string")
        return req["text"]

    server.register_handler(CommandSpec(
        "echo_text", lambda ctx, args: {"echo": args}, fields=frozenset({"text"}),
        parse=parse_text, default_data={"echo": "", "extra": 0},
    ))

    ok = _send(server, {"protocol_version": "1.0", "req_id": "e1", "device": "", "cmd": "echo_text", "text": "hi"})
    assert ok["status"] == "ok"
    assert ok["data"] == {"echo": "hi", "extra": 0}

    bad = _send(server, {"protocol_version": "1.0", "req_id": "e2", "device": "", "cmd": "echo_text", "text": 5})
    assert bad["error"] == "invalid_argument"
    assert bad["data"] == {"echo": "", "extra": 0}

    unknown = _send(server, {"protocol_version": "1.0", "req_id": "e3", "device": "", "cmd": "echo_text",
                             "text": "hi", "typo": 1})
    assert unknown["error"] == "unknown_field"
