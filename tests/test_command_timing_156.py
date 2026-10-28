"""Device command timing records in the Core log (issue #156, spec #154) plus the #159 review follow-ups."""
import asyncio
import logging
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bilingual_log import BilingualFormatter  # noqa: E402
from core_protocol import build_request, dumps_request  # noqa: E402
from fake_kvm import FakeKVMSession  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from kvm_timing import COMMAND_CONNECT, TimingRecord, classify_connect_error, log_timing  # noqa: E402
from jetkvm_core import KVMLoginFailed  # noqa: E402
from protocol_helpers import LogSolutionKVM, make_registry, recv_reply  # noqa: E402
from tcp_server import CommandSpec, TcpJsonServer  # noqa: E402

DEVICE = "DFU-01"
IP = "192.168.200.1"


class Session(FakeKVMSession):
    def __init__(self, delay=0.0, exc=None):
        self.delay, self.exc = delay, exc

    async def connect(self):
        await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        await super().connect()


def fmt(record) -> str:
    return BilingualFormatter().format(record)


def timing_records(caplog):
    return [r for r in caplog.records if r.name.startswith("KVMTiming")]


def fields(record) -> dict:
    return dict(tok.split("=", 1) for tok in record.getMessage().split() if "=" in tok)


def command_records(caplog):
    return [r for r in timing_records(caplog) if fields(r).get("path") == "command"]


class Rig:
    def __init__(self, caplog, factory=None, extra=()):
        self.caplog = caplog
        self.srv = TcpJsonServer(
            device_registry=make_registry(), host="127.0.0.1", port=0,
            kvm_pool=KVMConnectionPool(client_factory=factory or (lambda host, password="": Session())),
            freeze_guard=FrameFreezeGuard(freeze_threshold=0.3))
        for spec in extra:
            self.srv.register_handler(spec)

    def __enter__(self):
        self.caplog.set_level(logging.INFO, logger="KVMTiming")
        self.srv.start()
        return self

    def __exit__(self, *exc):
        self.srv.stop()
        self.srv.wait(5)

    def call(self, cmd, device=DEVICE, req_id="r1", payload=None):
        with socket.create_connection(self.srv.server_address, timeout=10) as s:
            s.sendall(dumps_request(build_request(cmd, req_id, device, payload)))
            return recv_reply(s)


def spec(name, handler, **kw):
    return CommandSpec(name, handler, device_bound=True, **kw)


async def slow_handler(ctx, args):
    await asyncio.sleep(0.2)
    return {"done": True}


def test_success_command_logs_one_line_with_device_and_ip(caplog):
    with Rig(caplog, extra=[spec("slow", slow_handler)]) as rig:
        reply = rig.call("slow")
    assert reply["status"] == "ok"
    (rec,) = command_records(caplog)
    f = fields(rec)
    assert (f["device"], f["ip"], f["command"], f["result"], f["source"]) == (DEVICE, IP, "slow", "ok", "client")
    assert rec.levelno == logging.INFO
    line = fmt(rec)
    assert "path=command command=slow source=client result=ok elapsed_ms=" in line
    assert "路徑=Core 指令處理 指令=slow 來源=用戶端 結果=成功 耗時=" in line


def test_elapsed_covers_receipt_to_reply_and_matches_envelope(caplog):
    with Rig(caplog, extra=[spec("slow", slow_handler)]) as rig:
        reply = rig.call("slow")
    (rec,) = command_records(caplog)
    ms = int(fields(rec)["elapsed_ms"])
    assert 190 <= ms <= 1000
    assert abs(ms - reply["elapsed_sec"] * 1000) <= 50


def test_device_busy_logs_fail(caplog):
    with Rig(caplog, extra=[spec("slow", slow_handler)]) as rig:
        assert rig.srv.lock_manager.try_acquire(DEVICE)
        reply = rig.call("slow")
    assert reply["error"] == "device_busy"
    (rec,) = command_records(caplog)
    f = fields(rec)
    assert (f["result"], f["reason"]) == ("fail", "device_busy")
    assert rec.levelno == logging.WARNING
    assert "原因=設備忙碌" in fmt(rec)


def test_kvm_connect_failure_logs_fail(caplog):
    with Rig(caplog, factory=lambda host, password="": Session(exc=OSError("down")),
             extra=[spec("slow", slow_handler)]) as rig:
        reply = rig.call("slow")
    assert reply["error"] == "kvm_connect_failed"
    (rec,) = command_records(caplog)
    assert (fields(rec)["result"], fields(rec)["reason"]) == ("fail", "kvm_connect_failed")
    assert "原因=KVM 連線失敗" in fmt(rec)


def test_long_poll_timeout_logs_timeout(caplog):
    from protocol_helpers import arm_round
    kvm = LogSolutionKVM()
    with Rig(caplog, factory=lambda host, password="": kvm) as rig:
        arm_round(rig.srv, kvm, DEVICE)
        reply = rig.call("check_finish", payload={"timeout_sec": 1.5})
    assert reply["error"] == "timeout"
    rec = command_records(caplog)[-1]  # the arming monitor_start has its own line before it
    f = fields(rec)
    assert (f["command"], f["result"], f["reason"]) == ("check_finish", "fail", "timeout")
    assert 1400 <= int(f["elapsed_ms"]) <= 3000


def test_client_disconnect_logs_one_line_without_reply(caplog):
    kvm = LogSolutionKVM()
    with Rig(caplog, factory=lambda host, password="": kvm) as rig:
        s = socket.create_connection(rig.srv.server_address, timeout=5)
        s.sendall(dumps_request(build_request("check_start", "r1", DEVICE, {"timeout_sec": 30})))
        time.sleep(0.5)
        s.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not command_records(caplog):
            time.sleep(0.05)
    (rec,) = command_records(caplog)
    f = fields(rec)
    assert (f["result"], f["reason"]) == ("fail", "client_disconnected")
    assert "原因=用戶端已中斷" in fmt(rec)


def test_handler_exception_logs_internal_error(caplog):
    def boom(ctx, args):
        raise RuntimeError("boom")
    with Rig(caplog, extra=[spec("boom", boom)]) as rig:
        reply = rig.call("boom")
    assert reply["error"] == "internal_error"
    (rec,) = command_records(caplog)
    assert (fields(rec)["result"], fields(rec)["reason"]) == ("fail", "internal_error")
    assert "原因=內部錯誤" in fmt(rec)


def test_non_device_and_invalid_requests_log_nothing(caplog):
    with Rig(caplog) as rig:
        assert rig.call("status", device="")["status"] == "ok"
        assert rig.call("core_status", device="")["status"] == "ok"
        assert rig.call("ping", device="")["status"] == "ok"
        assert rig.call("check_start", device="NOPE-99", payload={"timeout_sec": 1})["error"] == "unknown_device"
        with socket.create_connection(rig.srv.server_address, timeout=5) as s:
            s.sendall(b"not json\n")
            assert recv_reply(s)["error"] == "invalid_json"
    assert timing_records(caplog) == []


def test_new_connection_command_logs_connect_and_command(caplog):
    with Rig(caplog, factory=lambda host, password="": Session(delay=0.1),
             extra=[spec("slow", slow_handler)]) as rig:
        assert rig.call("slow")["status"] == "ok"
    recs = timing_records(caplog)
    assert [fields(r)["path"] for r in recs] == [COMMAND_CONNECT, "command"]
    assert int(fields(recs[1])["elapsed_ms"]) >= int(fields(recs[0])["elapsed_ms"])


def test_ui_request_is_source_ui_and_others_client(caplog):
    with Rig(caplog, extra=[spec("slow", slow_handler)]) as rig:
        rig.call("slow", req_id="ui-snap-1")
        rig.call("slow", req_id="r2")
    ui, client = command_records(caplog)
    assert fields(ui)["source"] == "ui" and fields(client)["source"] == "client"
    assert "來源=Monitor UI" in fmt(ui) and "來源=用戶端" in fmt(client)


def test_command_record_format_and_connect_lines_unchanged(caplog):
    ok = TimingRecord(1.0, "DFU-03", "192.168.1.10", "command", 85, True, command="check_start", source="client")
    bad = TimingRecord(1.0, "DFU-03", "192.168.1.10", "command", 85, False, "weird_code", command="check_start",
                       source="ui")
    with caplog.at_level(logging.INFO, logger="KVMTiming"):
        log_timing(ok)
        log_timing(bad)
    r_ok, r_bad = caplog.records
    assert fmt(r_ok)[9:] == (
        "| KVM timing device=DFU-03 ip=192.168.1.10 path=command command=check_start source=client result=ok "
        "elapsed_ms=85 | KVM 計時 設備=DFU-03 IP=192.168.1.10 路徑=Core 指令處理 指令=check_start 來源=用戶端 "
        "結果=成功 耗時=85 ms")
    assert "source=ui result=fail elapsed_ms=85 reason=weird_code" in fmt(r_bad)
    assert "來源=Monitor UI 結果=失敗 耗時=85 ms 原因=weird_code" in fmt(r_bad)


# --- #159 review follow-ups ---

def _chain(*excs):
    for outer, inner in zip(excs, excs[1:]):
        outer.__cause__ = inner
    return excs[0]


def test_wrapped_refused_connection_is_not_a_timeout_lookalikes():
    class ConnectTimeoutError(Exception):
        pass

    class NewConnectionError(ConnectTimeoutError):
        pass
    refused = _chain(RuntimeError("wrapped"), NewConnectionError("x"), ConnectionRefusedError())
    assert classify_connect_error(refused) == "connection_refused"
    assert classify_connect_error(_chain(RuntimeError("w"), NewConnectionError("x"))) == "unreachable"
    assert classify_connect_error(_chain(RuntimeError("w"), ConnectTimeoutError("x"))) == "timeout"


def test_wrapped_refused_connection_is_not_a_timeout_real_urllib3():
    urllib3_exc = pytest.importorskip("urllib3.exceptions")
    requests_exc = pytest.importorskip("requests.exceptions")
    requests = pytest.importorskip("requests")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    with pytest.raises(requests_exc.ConnectionError) as real:
        requests.get(f"http://127.0.0.1:{closed_port}/", timeout=2)
    assert classify_connect_error(real.value) == "connection_refused"
    unreachable = _chain(requests_exc.ConnectionError("wrapped"), urllib3_exc.NewConnectionError(None, "no route"),
                         OSError("no route"))
    assert classify_connect_error(unreachable) == "unreachable"
    assert classify_connect_error(requests_exc.ConnectTimeout("t")) == "timeout"
    assert classify_connect_error(_chain(requests_exc.ConnectionError("w"),
                                         urllib3_exc.ConnectTimeoutError(None, "t"))) == "timeout"
    assert classify_connect_error(requests_exc.ReadTimeout("t")) == "timeout"


def test_explicit_reason_wins_over_timeout_classification():
    class SlowLogin(KVMLoginFailed, TimeoutError):
        pass
    assert classify_connect_error(SlowLogin("x")) == "login_failed"
    assert classify_connect_error(_chain(TimeoutError("outer"), KVMLoginFailed("inner"))) == "login_failed"


def test_connect_elapsed_excludes_per_ip_lock_wait():
    now = [100.0]
    gate = asyncio.Event()
    records = []

    class First(FakeKVMSession):
        async def connect(self):
            await gate.wait()
            raise OSError("first fails")

    class Second(FakeKVMSession):
        async def connect(self):
            now[0] += 0.25
            await super().connect()
    sessions = iter([First(), Second()])
    pool = KVMConnectionPool(client_factory=lambda host, password="": next(sessions),
                             timing_observer=lambda *a: records.append(a), monotonic=lambda: now[0])

    async def run():
        first = asyncio.ensure_future(pool.acquire_connection(IP))
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(pool.acquire_connection(IP))
        await asyncio.sleep(0.05)
        now[0] += 50.0  # the second caller waits on the per-IP lock for 50 s
        gate.set()
        with pytest.raises(OSError):
            await first
        await second
    asyncio.run(run())
    first_ms, second_ms = (r[2] for r in records)
    assert first_ms == 50000  # the first attempt really spent 50 s connecting
    assert second_ms == 250  # the second one's 50 s wait for the lock is not part of its connect time


# --- review round 1: timing logging never touches the reply ---

def fast(ctx, args):
    return {"done": True}


class RaisingHandler(logging.Handler):
    def emit(self, record):
        raise OSError("disk full")


class RaisingFilter(logging.Filter):
    def filter(self, record):
        raise ValueError("bad filter")


class SleepingHandler(logging.Handler):
    def emit(self, record):
        time.sleep(0.5)


def _stable(reply):
    return {k: v for k, v in reply.items() if k != "elapsed_sec"}


@pytest.mark.parametrize("faulty", [RaisingHandler(), RaisingFilter()])
def test_faulty_log_handler_cannot_change_or_drop_the_reply(caplog, faulty):
    command_log = logging.getLogger("KVMTiming.command")
    attach = command_log.addFilter if isinstance(faulty, logging.Filter) else command_log.addHandler
    detach = command_log.removeFilter if isinstance(faulty, logging.Filter) else command_log.removeHandler
    with Rig(caplog, extra=[spec("fast", fast)]) as rig:
        baseline = rig.call("fast")
        attach(faulty)
        try:
            with socket.create_connection(rig.srv.server_address, timeout=5) as s:
                for req_id in ("r1", "r2"):  # the same connection keeps serving
                    s.sendall(dumps_request(build_request("fast", req_id, DEVICE)))
                    reply = recv_reply(s)
                    assert reply["status"] == "ok"
                    assert _stable(reply) == _stable({**baseline, "req_id": req_id})
        finally:
            detach(faulty)


def test_slow_log_handler_does_not_delay_the_reply(caplog):
    command_log = logging.getLogger("KVMTiming.command")
    slow = SleepingHandler()
    with Rig(caplog, extra=[spec("fast", fast)]) as rig:
        rig.call("fast")  # connect the KVM first
        command_log.addHandler(slow)
        try:
            began = time.monotonic()
            assert rig.call("fast")["status"] == "ok"
            assert time.monotonic() - began < 0.3
        finally:
            time.sleep(0.6)  # let the sleeping handler finish before the server stops
            command_log.removeHandler(slow)


class FakeReader:
    def __init__(self, line):
        self.lines = [line]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def at_eof(self):
        return not self.lines


class FakeWriter:
    def __init__(self, drain_error=None):
        self.drain_error, self.written = drain_error, b""

    def get_extra_info(self, name):
        return ("test", 0)

    def write(self, data):
        self.written += data

    async def drain(self):
        if self.drain_error is not None:
            raise self.drain_error

    def is_closing(self):
        return False

    def close(self):
        pass

    async def wait_closed(self):
        pass


def _session(caplog, writer):
    with Rig(caplog, extra=[spec("fast", fast)]) as rig:
        line = dumps_request(build_request("fast", "r1", DEVICE))
        fut = asyncio.run_coroutine_threadsafe(
            rig.srv._client_connected_cb(FakeReader(line), writer), rig.srv._loop)
        fut.result(10)
    return command_records(caplog)


def test_ok_is_logged_after_the_reply_was_written(caplog):
    writer = FakeWriter()
    (rec,) = _session(caplog, writer)
    assert writer.written and fields(rec)["result"] == "ok"


def test_failed_write_logs_client_disconnected(caplog):
    writer = FakeWriter(drain_error=ConnectionResetError())
    (rec,) = _session(caplog, writer)
    f = fields(rec)
    assert (f["result"], f["reason"]) == ("fail", "client_disconnected")


def test_exception_escaping_dispatch_logs_internal_error_and_sends_no_reply(caplog):
    async def broken(req_id, admitted, reader=None, writer=None):
        raise RuntimeError("dispatch broke")
    with Rig(caplog, extra=[spec("fast", fast)]) as rig:
        rig.srv._dispatch_command = broken
        with socket.create_connection(rig.srv.server_address, timeout=5) as s:
            s.sendall(dumps_request(build_request("fast", "r1", DEVICE)))
            assert s.recv(4096) == b""  # as on main: the session ends and nothing is written
    (rec,) = command_records(caplog)
    f = fields(rec)
    assert (f["result"], f["reason"]) == ("fail", "internal_error")
