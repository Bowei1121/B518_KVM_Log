"""fake_core 的 pytest：全部走真的 TCP socket，每次讀取都有硬逾時，不會卡住。"""

import ast
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_core  # noqa: E402

HERE = Path(__file__).resolve().parent
ENVELOPE_KEYS = ["protocol_version", "req_id", "device", "cmd", "status", "error", "message", "elapsed_sec", "data"]
UNKNOWN_DEFAULT = {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []}
READ_TIMEOUT = 10.0
DEV = "BT-01"


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=READ_TIMEOUT)
        self.buf = b""
        self.n = 0

    def send_raw(self, raw: bytes):
        self.sock.sendall(raw)

    def send(self, cmd, device=DEV, **fields):
        self.n += 1
        req = {"protocol_version": "1.0", "req_id": f"r{self.n}", "device": device, "cmd": cmd, **fields}
        self.send_raw((json.dumps(req) + "\n").encode())
        return req

    def recv_line(self):
        """一行原始回覆；對方關閉連線回 None；超過硬逾時丟 socket.timeout。"""
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line + b"\n"

    def recv(self):
        line = self.recv_line()
        assert line is not None, "connection closed without a reply"
        return json.loads(line)

    def call(self, cmd, device=DEV, **fields):
        self.send(cmd, device, **fields)
        return self.recv()

    def close(self):
        self.sock.close()


@pytest.fixture
def make_core():
    cores, clients = [], []

    def make(**kw):
        opts = dict(host="127.0.0.1", port=0, finish_delay=0.3, monitor_delay=0.0, not_found_delay=0.4, poll=0.05)
        opts.update(kw)
        core = fake_core.FakeCore(**opts)
        core.start()
        cores.append(core)
        core.connect = lambda: clients.append(Client(core.port)) or clients[-1]
        return core

    yield make
    for c in clients:
        c.close()
    for core in cores:
        core.stop()


@pytest.fixture
def core(make_core):
    return make_core()


@pytest.fixture
def cli(core):
    return core.connect()


def to_monitoring(cli):
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["status"] == "ok"
    return r


def data_of(r):
    return r["data"]


# ---------------------------------------------------------------- envelope / startup

def test_envelope_has_nine_keys_in_order_and_ends_with_lf(cli):
    cli.send("ping", device="")
    line = cli.recv_line()
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    reply = json.loads(line)
    assert list(reply) == ENVELOPE_KEYS
    assert reply["protocol_version"] == "1.0"
    assert reply["status"] == "ok" and reply["error"] == "" and reply["message"] == ""
    assert isinstance(reply["elapsed_sec"], float)
    assert reply["data"]["pong"] is True and reply["data"]["core_version"]


def test_default_port_is_5000_in_argparse():
    args = fake_core.build_parser().parse_args([])
    assert args.port == 5000 and args.host == "0.0.0.0"
    assert args.device == "BT-01" and args.station == "BT" and args.channels == 4
    assert args.finish_delay == 5 and args.monitor_delay == 0.5 and args.not_found_delay == 30 and args.poll == 0.5
    assert args.result == "PASS"


def test_port_zero_gets_real_port(core):
    assert core.port > 0 and core.port != 5000


def test_blank_lines_ignored_and_crlf_accepted(cli):
    cli.send_raw(b"\n\r\n")
    cli.send_raw(b'{"protocol_version":"1.0","req_id":"x","device":"","cmd":"ping"}\r\n')
    r = cli.recv()
    assert r["req_id"] == "x" and r["status"] == "ok"


def test_status_default_shape(cli):
    r = cli.call("status", device="")
    assert r["status"] == "ok"
    d = data_of(r)
    assert isinstance(d["core_uptime_sec"], int)
    assert d["devices"] == [{"device": DEV, "station": "BT", "connected": True, "busy": False, "frozen": False,
                             "channels": 4, "connect_failed": False, "connect_fail_count": 0}]


# ---------------------------------------------------------------- validation

def test_invalid_json(cli):
    cli.send_raw(b"not json\n")
    r = cli.recv()
    assert (r["status"], r["error"]) == ("error", "invalid_json")
    assert r["req_id"] == r["device"] == r["cmd"] == "" and r["data"] == {}
    assert list(r) == ENVELOPE_KEYS


def test_invalid_request_not_object(cli):
    cli.send_raw(b"[1,2]\n")
    r = cli.recv()
    assert r["error"] == "invalid_request" and r["data"] == {} and r["req_id"] == ""


def raw_req(**over):
    req = {"protocol_version": "1.0", "req_id": "v1", "device": DEV, "cmd": "check_finish", "timeout_sec": 0}
    req.update(over)
    return {k: v for k, v in req.items() if v is not None}


VALIDATION = [
    (raw_req(protocol_version="2.0"), "unsupported_protocol_version"),
    (raw_req(protocol_version=None), "unsupported_protocol_version"),
    (raw_req(req_id=None), "missing_req_id"),
    (raw_req(req_id=""), "missing_req_id"),
    (raw_req(req_id=5), "invalid_request"),
    (raw_req(cmd=None), "missing_command"),
    (raw_req(cmd=5), "invalid_request"),
    (raw_req(device=5), "invalid_request"),
    (raw_req(cmd="nope"), "unknown_command"),
    (raw_req(cmd="check"), "unknown_command"),
    (raw_req(bogus=1), "unknown_field"),
    (raw_req(kvm_ip="1.2.3.4"), "forbidden_field"),
    (raw_req(station="BT"), "forbidden_field"),
    (raw_req(cmd="ping", device="BT-01", timeout_sec=None), "invalid_argument"),
    (raw_req(device=""), "missing_device"),
    (raw_req(device="NOPE"), "unknown_device"),
    (raw_req(timeout_sec=3601), "invalid_argument"),
    (raw_req(timeout_sec=-1), "invalid_argument"),
    (raw_req(timeout_sec=None), "invalid_argument"),
    (raw_req(timeout_sec=True), "invalid_argument"),
    (raw_req(timeout_sec="5"), "invalid_argument"),
    (raw_req(cmd="monitor_start", timeout_sec=0), "invalid_argument"),
    (raw_req(cmd="monitor_start", timeout_sec=301), "invalid_argument"),
    (raw_req(cmd="monitor_start", timeout_sec=None), "invalid_argument"),
    (raw_req(cmd="start_test", timeout_sec=1), "unknown_field"),
]


@pytest.mark.parametrize("req,code", VALIDATION)
def test_validation_error_codes(cli, req, code):
    cli.send_raw((json.dumps(req) + "\n").encode())
    r = cli.recv()
    assert r["status"] == "error" and r["error"] == code and r["message"]
    assert list(r) == ENVELOPE_KEYS


def test_validation_errors_carry_command_default_data(cli):
    r = cli.call("check_finish", timeout_sec=3601)
    assert r["error"] == "invalid_argument" and r["data"] == UNKNOWN_DEFAULT
    assert r["message"] == "timeout_sec must be between 0 and 3600"
    r = cli.call("monitor_start", timeout_sec=0)
    assert r["data"] == {"state": "", "capacity": 0}
    assert r["message"] == "timeout_sec must be between 1 and 300"
    r = cli.call("nope")
    assert r["error"] == "unknown_command" and r["data"] == {} and r["cmd"] == "nope"
    assert r["message"] == "unknown command 'nope'"


def test_validation_order_is_version_before_req_id_before_cmd(cli):
    cli.send_raw(b'{"protocol_version":"9","cmd":"nope"}\n')
    assert cli.recv()["error"] == "unsupported_protocol_version"
    cli.send_raw(b'{"protocol_version":"1.0","cmd":"nope"}\n')
    assert cli.recv()["error"] == "missing_req_id"
    cli.send_raw(b'{"protocol_version":"1.0","req_id":"a"}\n')
    assert cli.recv()["error"] == "missing_command"


def test_fct_start_test_is_unsupported_command(make_core):
    c = make_core(station="FCT", device="FCT-01").connect()
    r = c.call("start_test", device="FCT-01")
    assert r["error"] == "unsupported_command" and r["data"] == {"action": ""}
    assert "FCT" in r["message"]


def test_fct_monitor_start_simulates_fixture_push_round_completes(make_core):
    c = make_core(station="FCT", device="FCT-01", finish_delay=2.5).connect()
    c.sock.settimeout(20)
    r = c.call("monitor_start", device="FCT-01", timeout_sec=5)
    assert r["status"] == "ok"
    r = c.call("check_start", device="FCT-01", timeout_sec=15)
    assert r["status"] == "ok" and r["data"]["kind"] == "started"
    r = c.call("check_finish", device="FCT-01", timeout_sec=15)
    assert r["status"] == "ok" and r["data"]["kind"] == "taken"


@pytest.mark.parametrize("cmd,fields", [
    ("snapshot", {"format": "png", "quality": 0}),
    ("inspect", {"timeout_sec": 0}),
])
def test_real_but_not_simulated_commands_say_so(cli, cmd, fields):
    r = cli.call(cmd, **fields)
    assert r["status"] == "error" and r["error"] == "unsupported_command"
    assert "fake core" in r["message"]


def test_input_bt_is_station_error_and_dfu_is_not_simulated(make_core, cli):
    assert "only supported for DFU" in cli.call("input", items=[])["message"]
    c = make_core(station="DFU", device="DFU-01").connect()
    r = c.call("input", device="DFU-01", items=[])
    assert r["error"] == "unsupported_command" and "fake core" in r["message"]


@pytest.mark.parametrize("cmd", ["core_status", "shutdown"])
def test_device_less_not_simulated_commands(cli, cmd):
    r = cli.call(cmd, device="")
    assert r["error"] == "unsupported_command" and "fake core" in r["message"]


# ---------------------------------------------------------------- happy path

def test_happy_path_bt(make_core):
    core = make_core(finish_delay=0.6)
    cli = core.connect()
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["status"] == "ok" and r["data"] == {"state": "monitoring", "capacity": 4}
    r = cli.call("start_test")
    assert r["status"] == "ok" and r["data"] == {"action": "start_all"}
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=10)
    waited = time.monotonic() - t0
    assert 0.4 <= waited < 3.0
    assert r["status"] == "ok"
    assert r["data"] == {"kind": "taken", "state": "complete", "reason": "reliable_complete", "capacity": 4,
                         "active": [], "results": [{"slot": i, "status": "PASS"} for i in range(1, 5)]}
    again = cli.call("check_finish", timeout_sec=0)
    assert again["data"]["kind"] == "already_taken" and again["data"]["reason"] == "round_already_taken"
    assert again["data"]["results"] == r["data"]["results"]
    assert again["data"]["state"] == "complete"


def test_happy_path_dfu_action_ok(make_core):
    c = make_core(station="DFU", device="DFU-01", channels=2).connect()
    assert c.call("monitor_start", device="DFU-01", timeout_sec=5)["data"] == {"state": "monitoring", "capacity": 2}
    assert c.call("start_test", device="DFU-01")["data"] == {"action": "ok"}
    r = c.call("check_finish", device="DFU-01", timeout_sec=10)
    assert r["data"]["kind"] == "taken" and len(r["data"]["results"]) == 2


def test_monitor_start_takes_monitor_delay(make_core):
    c = make_core(monitor_delay=0.4).connect()
    t0 = time.monotonic()
    to_monitoring(c)
    assert time.monotonic() - t0 >= 0.35


def test_check_finish_zero_while_running_is_waiting_monitoring(cli):
    to_monitoring(cli)
    cli.call("start_test")
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "ok"
    assert (r["data"]["kind"], r["data"]["state"], r["data"]["reason"]) == ("waiting", "monitoring", "monitoring")


def test_check_finish_timeout_before_result(make_core):
    c = make_core(finish_delay=30).connect()
    to_monitoring(c)
    c.call("start_test")
    t0 = time.monotonic()
    r = c.call("check_finish", timeout_sec=1)
    assert 0.9 <= time.monotonic() - t0 < 3
    assert (r["status"], r["error"]) == ("timeout", "timeout")
    assert r["message"] == "no reliable result within 1 s"
    assert r["data"]["kind"] == "waiting" and r["data"]["state"] == "monitoring"


@pytest.mark.parametrize("result,expected", [
    ("PASS", ["PASS"] * 4),
    ("FAIL", ["FAIL"] * 4),
    ("TIMEOUT", ["TIMEOUT"] * 4),
    ("PASS,FAIL,PASS,NOTEST", ["PASS", "FAIL", "PASS", "NOTEST"]),
])
def test_result_option(make_core, result, expected):
    c = make_core(result=result).connect()
    to_monitoring(c)
    c.call("start_test")
    r = c.call("check_finish", timeout_sec=10)
    assert [x["status"] for x in r["data"]["results"]] == expected
    assert [x["slot"] for x in r["data"]["results"]] == [1, 2, 3, 4]


def test_result_changed_at_runtime(core, cli):
    core.command("result FAIL")
    to_monitoring(cli)
    cli.call("start_test")
    r = cli.call("check_finish", timeout_sec=10)
    assert {x["status"] for x in r["data"]["results"]} == {"FAIL"}


# ---------------------------------------------------------------- not ready / busy

def test_check_finish_without_monitor_start_is_monitor_not_ready(cli):
    r = cli.call("check_finish", timeout_sec=0)
    assert r["error"] == "monitor_not_ready" and r["message"] == "no round-start request; result was not taken"
    assert r["data"]["kind"] == "unknown" and r["data"]["results"] == []
    assert r["data"]["state"] == "standby" and r["data"]["reason"] == "standby"


def test_start_test_when_not_monitoring_is_monitor_not_ready(cli):
    r = cli.call("start_test")
    assert r["error"] == "monitor_not_ready"
    assert r["message"] == "Log Solution is not monitoring (standby); no HID sent"
    assert r["data"] == {"action": ""}


def test_monitor_start_while_monitoring_is_busy(cli):
    to_monitoring(cli)
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_busy" and r["data"] == {"state": "", "capacity": 0}


def test_device_busy_for_second_locked_command(core):
    a, b = core.connect(), core.connect()
    to_monitoring(a)
    a.send("check_finish", timeout_sec=1)
    time.sleep(0.2)
    status = b.call("status", device="")
    assert status["data"]["devices"][0]["busy"] is True
    r = b.call("check_finish", timeout_sec=0)
    assert r["status"] == "error" and r["error"] == "device_busy" and r["message"] == ""
    assert r["data"] == UNKNOWN_DEFAULT
    r = b.call("monitor_start", timeout_sec=5)
    assert r["error"] == "device_busy" and r["data"] == {"state": "", "capacity": 0}
    assert a.recv()["status"] == "timeout"
    assert b.call("check_finish", timeout_sec=0)["error"] != "device_busy"


# ---------------------------------------------------------------- faults

def test_disconnect_fault_closes_clients_and_gives_no_reply(core):
    a, b = core.connect(), core.connect()
    to_monitoring(a)
    a.send("check_finish", timeout_sec=30)
    time.sleep(0.2)
    assert core.command("disconnect")
    assert a.recv_line() is None
    assert b.recv_line() is None
    # 伺服器仍在聽
    c = core.connect()
    assert c.call("ping", device="")["data"]["pong"] is True
    # 進行中請求被取消，鎖已釋放
    assert c.call("check_finish", timeout_sec=0)["error"] != "device_busy"


def test_timeout_fault_waits_full_timeout(make_core):
    core = make_core(finish_delay=0.1)
    cli = core.connect()
    to_monitoring(cli)
    cli.call("start_test")
    core.command("timeout")
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=1)
    assert time.monotonic() - t0 >= 0.95
    assert (r["status"], r["error"]) == ("timeout", "timeout")
    assert r["data"]["kind"] == "waiting" and r["data"]["state"] == "monitoring"
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "ok" and r["data"]["kind"] == "waiting"
    core.command("clear")
    assert cli.call("check_finish", timeout_sec=5)["data"]["kind"] == "taken"


def test_heartbeat_fault_status_and_commands(core, cli):
    core.command("heartbeat")
    d = cli.call("status", device="")["data"]["devices"][0]
    assert d["frozen"] is True and d["connected"] is False
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "kvm_connect_failed" and "connection lost during the command" in r["message"]
    assert cli.call("check_finish", timeout_sec=0)["error"] == "kvm_connect_failed"
    core.command("clear")
    d = cli.call("status", device="")["data"]["devices"][0]
    assert d["frozen"] is False and d["connected"] is True


def test_connect_failed_fault(core, cli):
    core.command("connect_failed")
    d = cli.call("status", device="")["data"]["devices"][0]
    assert d["connect_failed"] is True and d["connect_fail_count"] == 3 and d["connected"] is False
    for cmd, fields, default in [
        ("monitor_start", {"timeout_sec": 5}, {"state": "", "capacity": 0}),
        ("start_test", {}, {"action": ""}),
        ("check_finish", {"timeout_sec": 0}, UNKNOWN_DEFAULT),
    ]:
        r = cli.call(cmd, **fields)
        assert r["error"] == "kvm_connect_failed"
        assert r["message"] == "Failed to connect to KVM 'fake': simulated connect failure"
        assert r["data"] == default
    core.command("clear")
    assert cli.call("monitor_start", timeout_sec=5)["status"] == "ok"


def test_review_fault_paused_immediately_even_on_long_poll(core, cli):
    to_monitoring(cli)
    cli.call("start_test")
    core.command("review")
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=30)
    assert time.monotonic() - t0 < 1.0
    assert r["status"] == "ok"
    assert r["data"] == {"kind": "paused", "state": "review", "reason": "confirmation_required", "capacity": 4,
                         "active": [], "results": []}


def test_review_works_after_results_shown(make_core):
    core = make_core(finish_delay=0.1)
    cli = core.connect()
    to_monitoring(cli)
    cli.call("start_test")
    time.sleep(0.3)
    core.command("review")
    assert cli.call("check_finish", timeout_sec=0)["data"]["kind"] == "paused"


def test_confirm_complete_t0_twice_within_one_second(core, cli):
    to_monitoring(cli)
    core.command("review")
    core.command("confirm")
    first = cli.call("check_finish", timeout_sec=0)
    assert first["status"] == "ok"
    assert (first["data"]["kind"], first["data"]["state"], first["data"]["reason"]) == \
        ("waiting", "complete", "confirming_complete_frame")
    second = cli.call("check_finish", timeout_sec=0)
    assert second["data"]["kind"] == "taken" and second["data"]["reason"] == "reliable_complete"
    assert len(second["data"]["results"]) == 4
    third = cli.call("check_finish", timeout_sec=0)
    assert third["data"]["kind"] == "already_taken"


def test_confirm_complete_long_poll_takes_on_second_poll(core, cli):
    to_monitoring(cli)
    core.command("review")
    core.command("confirm complete")
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=10)
    assert time.monotonic() - t0 < 1.0
    assert r["data"]["kind"] == "taken"


def test_confirm_complete_polls_more_than_one_second_apart_never_take(core, cli):
    to_monitoring(cli)
    core.command("review")
    core.command("confirm")
    for _ in range(2):
        r = cli.call("check_finish", timeout_sec=0)
        assert (r["data"]["kind"], r["data"]["reason"]) == ("waiting", "confirming_complete_frame")
        time.sleep(1.15)


def test_confirm_standby_round_lost_then_waiting_standby(core, cli):
    to_monitoring(cli)
    core.command("review")
    core.command("confirm standby")
    first = cli.call("check_finish", timeout_sec=0)
    assert first["status"] == "error" and first["error"] == "round_lost"
    assert first["message"] == "Log Solution returned to standby before this round was taken"
    assert first["data"] == UNKNOWN_DEFAULT
    second = cli.call("check_finish", timeout_sec=0)
    assert second["status"] == "ok"
    assert (second["data"]["kind"], second["data"]["state"], second["data"]["reason"]) == \
        ("waiting", "standby", "standby")


def test_monitor_start_during_review_then_confirm_is_manual_verification(core, cli):
    to_monitoring(cli)
    core.command("review")
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_review" and r["data"] == {"state": "", "capacity": 0}
    assert r["message"] == "Log Solution 為待確認（開始前），未繼續開始監控"
    core.command("confirm")
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "error" and r["error"] == "manual_verification_required"
    assert r["message"] == "start was requested but this round's monitoring was not seen"
    assert r["data"] == {**UNKNOWN_DEFAULT, "state": "complete", "reason": "deliverable_without_monitoring",
                         "capacity": 4}
    # 本輪開始請求已清除
    r = cli.call("check_finish", timeout_sec=0)  # d8
    assert r["status"] == "error" and r["error"] == "monitor_not_ready"
    assert r["message"] == "no round-start request; result was not taken"
    assert r["data"] == {**UNKNOWN_DEFAULT, "state": "complete", "reason": "deliverable_without_monitoring",
                         "capacity": 4}


def test_confirm_standby_then_long_poll_times_out_with_waiting_standby(core, cli):  # a8
    to_monitoring(cli)
    core.command("review")
    core.command("confirm standby")
    assert cli.call("check_finish", timeout_sec=0)["error"] == "round_lost"
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=1)
    assert time.monotonic() - t0 >= 0.95
    assert (r["status"], r["error"]) == ("timeout", "timeout")
    assert r["data"] == {**UNKNOWN_DEFAULT, "kind": "waiting", "state": "standby", "reason": "standby",
                         "capacity": 4}


def test_slow_polls_then_long_poll_takes_within_a_second(core, cli):  # b-slow
    to_monitoring(cli)
    core.command("review")
    core.command("confirm")
    for _ in range(2):
        r = cli.call("check_finish", timeout_sec=0)
        assert r["data"]["reason"] == "confirming_complete_frame"
        time.sleep(1.15)
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=5)
    assert time.monotonic() - t0 < 1.0
    assert r["data"]["kind"] == "taken"


# tcp_server._default_specs 的 default_data，逐字抄來
TCP_SERVER_DEFAULTS = {
    "ping": {"pong": False, "core_version": ""},
    "status": {"core_uptime_sec": 0, "devices": []},
    "monitor_start": {"state": "", "capacity": 0},
    "start_test": {"action": ""},
    "check_start": {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []},
    "check_finish": {"kind": "unknown", "state": "", "reason": "", "capacity": 0, "active": [], "results": []},
}


def test_error_replies_carry_exactly_tcp_server_default_data_keys(core, cli):
    a = core.connect()
    to_monitoring(a)
    a.send("check_finish", timeout_sec=1)  # 佔住鎖 → 其他人 device_busy
    time.sleep(0.2)
    fields = {"monitor_start": {"timeout_sec": 5}, "check_finish": {"timeout_sec": 0},
              "check_start": {"timeout_sec": 0}}
    for cmd, default in TCP_SERVER_DEFAULTS.items():
        bad = cli.call(cmd, device="BAD")  # unknown_device / invalid_argument（ping、status 帶 device）
        assert bad["status"] == "error" and bad["data"] == default, cmd
        if cmd in fields or cmd == "start_test":
            busy = cli.call(cmd, **fields.get(cmd, {}))
            assert busy["error"] == "device_busy" and busy["data"] == default, cmd
            assert list(busy["data"]) == list(default), cmd
    a.recv()


def test_shutdown_fault_in_flight_and_new_requests(core):
    a, b = core.connect(), core.connect()
    to_monitoring(a)
    a.send("check_finish", timeout_sec=30)
    time.sleep(0.2)
    core.command("shutdown")
    r = a.recv()
    assert r["status"] == "error" and r["error"] == "server_shutting_down" and r["message"] == ""
    assert r["data"] == UNKNOWN_DEFAULT
    for cmd, fields in [("ping", {}), ("check_finish", {"timeout_sec": 0}), ("monitor_start", {"timeout_sec": 5})]:
        r = b.call(cmd, device="" if cmd == "ping" else DEV, **fields)
        assert r["error"] == "server_shutting_down"
    assert "standby" in core.command("state")
    core.command("clear")
    assert b.call("ping", device="")["status"] == "ok"


def test_not_found_fault_monitor_start(core, cli):
    core.command("not_found")
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_not_found" and r["message"] == "Log Solution Dock icon is not on screen"
    assert r["data"] == {"state": "", "capacity": 0}


def test_not_found_fault_check_finish_zero(core, cli):
    core.command("not_found")
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "ok"
    assert r["data"] == {**UNKNOWN_DEFAULT, "reason": "locators_missing_or_ambiguous"}


def test_not_found_fault_long_poll_waits_then_errors(core, cli):
    core.command("not_found")
    t0 = time.monotonic()
    r = cli.call("check_finish", timeout_sec=30)
    waited = time.monotonic() - t0
    assert 0.35 <= waited < 3
    assert r["error"] == "log_solution_not_found"
    assert r["data"] == {**UNKNOWN_DEFAULT, "reason": "locators_missing_or_ambiguous"}


def test_not_found_fault_timeout_first_when_smaller(core, cli):
    core.command("not_found")
    r = cli.call("check_finish", timeout_sec=0.15)
    assert r["status"] == "timeout" and r["error"] == "timeout"


def test_not_found_fault_start_test_unreadable(core, cli):
    core.command("not_found")
    r = cli.call("start_test")
    assert r["error"] == "monitor_not_ready" and "(unreadable)" in r["message"]


def test_reset_clears_faults_and_round(core, cli):
    to_monitoring(cli)
    core.command("heartbeat")
    out = core.command("reset")
    assert out
    assert cli.call("check_finish", timeout_sec=0)["error"] == "monitor_not_ready"


def test_command_help_state_and_unknown(core):
    assert "disconnect" in core.command("help")
    assert "standby" in core.command("state")
    assert core.command("no_such_thing")


def test_listener_hook_gets_events(core, cli):
    events = []
    core.add_listener(lambda name, dev, details: events.append((name, dev)))
    to_monitoring(cli)
    cli.call("start_test")
    cli.call("check_finish", timeout_sec=10)
    names = [e[0] for e in events]
    assert "monitor_start" in names and "start_test" in names and "taken" in names
    assert all(e[1] == DEV for e in events)


def test_public_transition_methods(core, cli):
    dev = core.devices[DEV]
    dev.enter_review()
    assert cli.call("check_finish", timeout_sec=0)["data"]["kind"] == "paused"
    dev.confirm("standby")
    assert dev.screen == "standby"


# ---------------------------------------------------------------- stdin / stdlib

def test_fault_from_stdin_in_subprocess():
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "fake_core.py"), "--port", "0", "--host", "127.0.0.1",
         "--finish-delay", "0.2", "--monitor-delay", "0"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8")
    try:
        port = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if "listening on" in line:
                port = int(line.strip().rsplit(":", 1)[1])
                break
        assert port, "no 'listening on HOST:PORT' line"
        drain = threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True)
        drain.start()
        c = Client(port)
        d = c.call("status", device="")["data"]["devices"][0]
        assert d["frozen"] is False and d["connected"] is True
        proc.stdin.write("heartbeat\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            d = c.call("status", device="")["data"]["devices"][0]
            if d["frozen"]:
                break
            time.sleep(0.05)
        assert d["frozen"] is True and d["connected"] is False
        # 關掉 stdin（EOF）後伺服器仍要繼續服務
        proc.stdin.close()
        time.sleep(0.3)
        assert proc.poll() is None
        assert c.call("ping", device="")["data"]["pong"] is True
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_only_stdlib_imports():
    tree = ast.parse((HERE / "fake_core.py").read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            mods.add((node.module or "").split(".")[0])
    assert mods
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


# ---------------------------------------------------------------- check_start

WAITING_MONITORING = {"kind": "waiting", "state": "monitoring", "reason": "monitoring", "capacity": 4,
                      "active": [], "results": []}
STARTED = {"kind": "started", "state": "monitoring", "reason": "activity_confirmed", "capacity": 4,
           "active": [{"slot": i, "evidence": "testing"} for i in range(1, 5)], "results": []}
PAUSED = {"kind": "paused", "state": "review", "reason": "confirmation_required", "capacity": 4,
          "active": [], "results": []}
PASS4 = [{"slot": i, "status": "PASS"} for i in range(1, 5)]


def running(make_core, **kw):
    """monitor_start + start_test 之後的 (core, client)。"""
    core = make_core(**kw)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    return core, cli


@pytest.mark.parametrize("fields,msg", [
    ({}, "timeout_sec is required and must be a number"),
    ({"timeout_sec": True}, "timeout_sec is required and must be a number"),
    ({"timeout_sec": -1}, "timeout_sec must be between 0 and 3600"),
    ({"timeout_sec": 3601}, "timeout_sec must be between 0 and 3600"),
])
def test_check_start_timeout_validation(cli, fields, msg):
    r = cli.call("check_start", **fields)
    assert r["error"] == "invalid_argument" and r["message"] == msg and r["data"] == UNKNOWN_DEFAULT


def test_check_start_unknown_field(cli):
    r = cli.call("check_start", timeout_sec=0, bogus=1)
    assert r["error"] == "unknown_field" and r["data"] == UNKNOWN_DEFAULT


def test_check_start_device_busy(core, cli):
    a = core.connect()
    to_monitoring(a)
    a.send("check_start", timeout_sec=1)
    time.sleep(0.2)
    r = cli.call("check_start", timeout_sec=0)
    assert r["error"] == "device_busy" and r["data"] == UNKNOWN_DEFAULT
    assert a.recv()["status"] == "timeout"


def test_check_start_without_monitor_start_is_monitor_not_ready(cli):
    r = cli.call("check_start", timeout_sec=0)
    assert r["error"] == "monitor_not_ready" and r["data"]["kind"] == "unknown" and r["data"]["results"] == []


def test_check_start_after_monitor_start_only_is_waiting_then_times_out(cli):
    to_monitoring(cli)
    r = cli.call("check_start", timeout_sec=0)
    assert r["status"] == "ok" and r["data"] == WAITING_MONITORING
    t0 = time.monotonic()
    r = cli.call("check_start", timeout_sec=1)
    assert time.monotonic() - t0 >= 0.95
    assert (r["status"], r["error"]) == ("timeout", "timeout")
    assert r["message"] == "no reliable result within 1 s" and r["data"] == WAITING_MONITORING


def test_check_start_long_poll_started_exact_data(make_core):
    core, cli = running(make_core, finish_delay=30)
    t0 = time.monotonic()
    r = cli.call("check_start", timeout_sec=5)
    assert time.monotonic() - t0 < 1.0
    assert r["status"] == "ok" and r["data"] == STARTED
    again = cli.call("check_start", timeout_sec=0)
    assert again["data"] == STARTED


def test_check_start_two_polls_within_one_second_pair(make_core):
    core, cli = running(make_core, finish_delay=30)
    assert cli.call("check_start", timeout_sec=0)["data"] == WAITING_MONITORING
    assert cli.call("check_start", timeout_sec=0)["data"] == STARTED


def test_check_start_polls_more_than_one_second_apart_never_pair(make_core):
    core, cli = running(make_core, finish_delay=30)
    assert cli.call("check_start", timeout_sec=0)["data"] == WAITING_MONITORING
    time.sleep(1.15)
    assert cli.call("check_start", timeout_sec=0)["data"] == WAITING_MONITORING


def test_started_does_not_consume_round_and_check_finish_never_reports_started(make_core):
    core, cli = running(make_core, finish_delay=2.5)
    assert cli.call("check_start", timeout_sec=5)["data"]["kind"] == "started"
    r = cli.call("check_finish", timeout_sec=0)
    assert r["data"]["kind"] == "waiting" and r["data"]["active"] == []
    r = cli.call("check_finish", timeout_sec=15)
    assert r["data"]["kind"] == "taken" and r["data"]["results"] == PASS4 and r["data"]["active"] == []


def test_check_start_on_complete_screen_takes_then_already_taken(make_core):
    core, cli = running(make_core, finish_delay=0.1)
    time.sleep(0.3)
    r = cli.call("check_start", timeout_sec=5)
    assert r["data"] == {"kind": "taken", "state": "complete", "reason": "reliable_complete", "capacity": 4,
                         "active": [], "results": PASS4}
    r = cli.call("check_finish", timeout_sec=0)
    assert r["data"]["kind"] == "already_taken" and r["data"]["results"] == PASS4
    r = cli.call("check_start", timeout_sec=0)
    assert r["data"]["kind"] == "already_taken" and r["data"]["reason"] == "round_already_taken"


def test_check_start_during_review_is_paused_even_on_long_poll(core, cli):
    to_monitoring(cli)
    core.command("review")
    t0 = time.monotonic()
    r = cli.call("check_start", timeout_sec=30)
    assert time.monotonic() - t0 < 1.0 and r["status"] == "ok" and r["data"] == PAUSED


def test_check_start_shutdown_fault(core):
    a, b = core.connect(), core.connect()
    to_monitoring(a)
    a.send("check_start", timeout_sec=30)
    time.sleep(0.2)
    core.command("shutdown")
    r = a.recv()
    assert r["error"] == "server_shutting_down" and r["data"] == UNKNOWN_DEFAULT
    assert b.call("check_start", timeout_sec=0)["error"] == "server_shutting_down"


def test_check_start_heartbeat_fault(core, cli):
    core.command("heartbeat")
    r = cli.call("check_start", timeout_sec=0)
    assert r["error"] == "kvm_connect_failed" and r["data"] == UNKNOWN_DEFAULT


def test_check_start_not_found_fault(core, cli):
    core.command("not_found")
    r = cli.call("check_start", timeout_sec=0)
    assert r["status"] == "ok" and r["data"] == {**UNKNOWN_DEFAULT, "reason": "locators_missing_or_ambiguous"}


def test_check_start_timeout_fault(make_core):
    core, cli = running(make_core, finish_delay=30)
    core.command("timeout")
    t0 = time.monotonic()
    r = cli.call("check_start", timeout_sec=1)
    assert time.monotonic() - t0 >= 0.95
    assert (r["status"], r["error"]) == ("timeout", "timeout") and r["data"]["kind"] == "waiting"


def test_check_start_client_gone_releases_lock(core):
    a = core.connect()
    to_monitoring(a)
    a.send("check_start", timeout_sec=30)
    time.sleep(0.2)
    a.close()
    time.sleep(0.3)
    assert core.connect().call("check_start", timeout_sec=0)["error"] != "device_busy"


# ---------------------------------------------------------------- scenarios

def test_scenario_cli_option_and_constructor():
    parser = fake_core.build_parser()
    assert parser.parse_args([]).scenario is None
    assert parser.parse_args(["--scenario", "cs_taken"]).scenario == "cs_taken"
    assert parser.parse_args(["--scenario", "stdrop"]).scenario == "stdrop"
    with pytest.raises(SystemExit):
        parser.parse_args(["--scenario", "nope"])
    with pytest.raises(ValueError):
        fake_core.FakeCore(port=0, scenario="nope")


def test_scenario_cs_started_is_normal_and_clears_scenario(make_core):
    core = make_core(scenario="cs_timeout", finish_delay=30)
    core.command("cs_started")
    assert "cs_timeout" not in core.command("state")
    cli = core.connect()
    to_monitoring(cli)
    cli.call("start_test")
    cli.call("check_start", timeout_sec=0)
    assert cli.call("check_start", timeout_sec=0)["data"] == STARTED


def test_scenario_cs_taken(make_core):
    core = make_core(scenario="cs_taken", finish_delay=30)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    first = cli.call("check_start", timeout_sec=0)
    assert (first["data"]["kind"], first["data"]["state"], first["data"]["reason"]) == \
        ("waiting", "complete", "confirming_complete_frame")
    r = cli.call("check_start", timeout_sec=0)
    assert r["data"]["kind"] == "taken" and r["data"]["results"] == PASS4
    for t in (0, 5):
        r = cli.call("check_finish", timeout_sec=t)
        assert r["data"]["kind"] == "already_taken" and r["data"]["results"] == PASS4
    assert cli.call("check_start", timeout_sec=0)["data"]["kind"] == "already_taken"


def test_scenario_cs_taken_long_poll_alias(make_core):
    core = make_core(finish_delay=30)
    core.command("scenario cstk")
    cli = core.connect()
    to_monitoring(cli)
    cli.call("start_test")
    r = cli.call("check_start", timeout_sec=5)
    assert r["data"]["kind"] == "taken"


@pytest.mark.parametrize("typed", ["cs_timeout", "csto", "scenario cs_timeout", "scenario csto"])
def test_scenario_cs_timeout(make_core, typed):
    core = make_core(finish_delay=0.1)
    core.command(typed)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    time.sleep(0.3)
    assert cli.call("check_start", timeout_sec=0)["data"] == WAITING_MONITORING
    r = cli.call("check_start", timeout_sec=0.3)
    assert (r["status"], r["error"]) == ("timeout", "timeout") and r["data"] == WAITING_MONITORING
    r = cli.call("check_finish", timeout_sec=0.3)
    assert (r["status"], r["error"]) == ("timeout", "timeout") and r["data"] == WAITING_MONITORING
    assert cli.call("check_finish", timeout_sec=0)["status"] == "ok"


def test_scenario_cs_paused(make_core):
    core = make_core(scenario="csp", finish_delay=0.5)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    assert cli.call("check_start", timeout_sec=0)["data"] == WAITING_MONITORING
    r = cli.call("check_start", timeout_sec=10)
    assert r["status"] == "ok" and r["data"] == PAUSED
    assert cli.call("check_start", timeout_sec=0)["data"] == PAUSED
    core.command("confirm standby")
    assert cli.call("check_finish", timeout_sec=0)["error"] == "round_lost"


def test_scenario_cs_paused_confirm_complete(make_core):
    core = make_core(scenario="cs_paused", finish_delay=0.1)
    cli = core.connect()
    to_monitoring(cli)
    cli.call("start_test")
    time.sleep(0.3)
    assert cli.call("check_start", timeout_sec=0)["data"] == PAUSED
    core.command("confirm")
    assert cli.call("check_finish", timeout_sec=5)["data"]["kind"] == "taken"


@pytest.mark.parametrize("name", ["start_test_drop_not_started", "stdrop"])
def test_scenario_start_test_drop_not_started(make_core, name):
    core = make_core(scenario=name, finish_delay=0.1)
    cli = core.connect()
    to_monitoring(cli)
    cli.send("start_test")
    assert cli.recv_line() is None  # 沒有回覆、連線被關
    cli2 = core.connect()
    r = cli2.call("check_start", timeout_sec=1)
    assert (r["status"], r["error"]) == ("timeout", "timeout") and r["data"] == WAITING_MONITORING
    r = cli2.call("check_finish", timeout_sec=0)
    assert r["status"] == "ok" and r["data"]["kind"] == "waiting"  # 不是 monitor_not_ready


@pytest.mark.parametrize("name", ["start_test_drop", "stds"])
def test_scenario_start_test_drop_test_runs_normally(make_core, name):
    core = make_core(scenario=name, finish_delay=2.5)
    assert core.scenario == "start_test_drop"
    cli = core.connect()
    to_monitoring(cli)
    cli.send("start_test")
    assert cli.recv_line() is None  # 沒有回覆、連線被關
    cli2 = core.connect()
    r = cli2.call("check_start", timeout_sec=5)
    assert r["status"] == "ok" and r["data"] == STARTED
    r = cli2.call("check_finish", timeout_sec=15)
    assert r["data"]["kind"] == "taken" and r["data"]["results"] == PASS4
    again = cli2.call("check_finish", timeout_sec=0)
    assert again["data"]["kind"] == "already_taken" and again["data"]["results"] == PASS4


def test_scenario_start_test_drop_cli_and_typed(core):
    assert fake_core.build_parser().parse_args(["--scenario", "start_test_drop"]).scenario == "start_test_drop"
    assert fake_core.build_parser().parse_args(["--scenario", "stds"]).scenario == "stds"
    core.command("stds")
    assert "start_test_drop" in core.command("state")
    core.command("scenario start_test_drop_not_started")
    assert core.scenario == "start_test_drop_not_started"
    core.command("scenario stds")
    assert core.scenario == "start_test_drop"
    core.command("clear")
    assert core.scenario is None


def test_scenario_clear_and_reset_remove_it(core):
    core.command("cs_taken")
    assert "cs_taken" in core.command("state")
    core.command("clear")
    assert "cs_taken" not in core.command("state")
    core.command("stdrop")
    core.command("reset")
    assert "stdrop" not in core.command("state")


def test_scenario_unknown_name_and_help(core):
    out = core.command("scenario bogus")
    assert "bogus" in out and "cs_taken" in out
    assert core.scenario is None
    for name in ("cs_started", "cs_taken", "cs_timeout", "cs_paused", "start_test_drop_not_started",
                 "start_test_drop (stds)"):
        assert name in core.command("help")


def test_scenario_listener_event(core):
    events = []
    core.add_listener(lambda n, d, details: events.append((n, details)))
    core.command("csp")
    assert ("scenario", {"scenario": "cs_paused"}) in events


def test_scenario_from_stdin_in_subprocess():
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "fake_core.py"), "--port", "0", "--host", "127.0.0.1",
         "--finish-delay", "0.2", "--monitor-delay", "0"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        encoding="utf-8")
    try:
        port = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if "listening on" in line:
                port = int(line.strip().rsplit(":", 1)[1])
                break
        assert port
        threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
        proc.stdin.write("scenario cs_taken\nresult FAIL\n")  # 指令依序處理：結果為 FAIL 時情境必已生效
        proc.stdin.flush()
        c = Client(port)
        got = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and got is None:
            c.call("monitor_start", timeout_sec=5)
            c.call("start_test")
            r = c.call("check_start", timeout_sec=3)
            if r["data"]["kind"] == "taken":
                got = r
            else:
                time.sleep(0.2)
        assert got is not None and {x["status"] for x in got["data"]["results"]} == {"FAIL"}
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------- monitor_start 丟掉上一輪（同真 Core 的 begin_start）


def test_monitor_start_not_found_still_drops_previous_round(make_core):
    core = make_core(finish_delay=0.1)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    r = cli.call("check_finish", timeout_sec=15)
    assert r["data"]["kind"] == "taken"
    core.command("nf")
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_not_found"
    core.command("clear")
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "error" and r["error"] == "monitor_not_ready"
    r = cli.call("check_finish", timeout_sec=0)
    assert r["status"] == "error" and r["error"] == "monitor_not_ready"
    assert r["data"]["kind"] != "already_taken"


def test_monitor_start_busy_then_check_during_test_rearms_and_takes(make_core):
    core = make_core(finish_delay=2.5)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_busy"
    r = cli.call("check_finish", timeout_sec=15)
    assert r["status"] == "ok" and r["data"]["kind"] == "taken"


def test_monitor_start_busy_then_no_check_until_complete_manual_verification(make_core):
    core = make_core(finish_delay=2.5)
    cli = core.connect()
    to_monitoring(cli)
    assert cli.call("start_test")["status"] == "ok"
    r = cli.call("monitor_start", timeout_sec=5)
    assert r["error"] == "log_solution_busy"
    time.sleep(2.5 + 1.0)  # 測試結束、完成畫面出現，但這段時間沒有任何查詢
    r = cli.call("check_finish", timeout_sec=15)
    assert r["status"] == "error" and r["error"] == "manual_verification_required"
    assert r["data"]["kind"] != "taken"


# ---------------------------------------------------------------- 支援 BT／DFU／FCT（未知站別被拒絕）


@pytest.mark.parametrize("station", ["BT", "DFU"])
def test_cli_accepts_bt_and_dfu_station(station):
    args = fake_core.build_parser().parse_args(["--station", station])
    assert args.station == station


def test_cli_accepts_fct_station():
    parser = fake_core.build_parser()
    assert parser.parse_args(["--station", "FCT"]).station == "FCT"
    assert parser.parse_args(["--station", "fct"]).station == "FCT"


def test_cli_rejects_unknown_station(capsys):
    with pytest.raises(SystemExit) as exc:
        fake_core.main(["--station", "XYZ", "--port", "0"])
    assert exc.value.code not in (0, None)
    err = capsys.readouterr().err
    assert "BT" in err and "DFU" in err and "FCT" in err


def test_typed_station_command_does_not_switch(core):
    before = core.command("state")
    out = core.command("station FCT")
    assert "FCT" in out and "不支援" in out and "切換" in out
    assert "切換" in core.command("station DFU")
    assert core.command("state") == before
    assert core.devices[DEV].station == "BT"
