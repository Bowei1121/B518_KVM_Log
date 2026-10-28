"""Connection timing records in the Core log (issue #155, spec #154)."""
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
from connect_failures import ConnectFailures  # noqa: E402
from fake_kvm import FakeKVMSession  # noqa: E402
from jetkvm_core import KVMLoginFailed, KVMNoSignal, KVMRpcNotOpen  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from kvm_timing import (  # noqa: E402
    BACKGROUND_RECONNECT, COMMAND_CONNECT, TimingRecord, classify_connect_error, log_timing)
from protocol_helpers import make_registry, recv_reply  # noqa: E402
from core_protocol import build_request, dumps_request  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

IP = "192.168.1.10"


class Mono:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Epoch:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Session(FakeKVMSession):
    def __init__(self, mono=None, takes=0.0, exc=None, hang=False):
        self.mono, self.takes, self.exc, self.hang = mono, takes, exc, hang

    async def connect(self):
        if self.mono is not None:
            self.mono.now += self.takes
        if self.hang:
            await asyncio.sleep(30)
        if self.exc is not None:
            raise self.exc
        await super().connect()


def make_pool(session_factory, mono=None, **kw):
    records = []
    epoch = Epoch()
    pool = KVMConnectionPool(
        client_factory=lambda host, password="": session_factory(),
        connect_failures=ConnectFailures(clock=epoch),
        timing_observer=lambda *a: records.append(a),
        monotonic=mono or time.monotonic, **kw)
    return pool, records, epoch


def due(pool, epoch):
    epoch.now += 1000


def test_background_reconnect_success_records_elapsed():
    mono = Mono()
    pool, records, epoch = make_pool(lambda: Session(mono, 1.24), mono)
    pool.connect_failures.record_failure(IP)
    due(pool, epoch)
    asyncio.run(pool.retry_due_connections())
    assert len(records) == 1
    ip, path, ms, ok, reason, exc = records[0]
    assert (ip, path, ms, ok, reason, exc) == (IP, BACKGROUND_RECONNECT, 1240, True, "", None)


def test_background_reconnect_timeout_records_fail():
    pool, records, epoch = make_pool(lambda: Session(hang=True), connect_timeout_sec=0.05)
    pool.connect_timeout_sec = 0.05
    pool.connect_failures.record_failure(IP)
    due(pool, epoch)
    asyncio.run(pool.retry_due_connections())
    assert len(records) == 1
    _, path, ms, ok, reason, exc = records[0]
    assert (path, ok, reason) == (BACKGROUND_RECONNECT, False, "timeout")
    assert 40 <= ms <= 500
    assert isinstance(exc, TimeoutError)


def test_command_path_success_and_failure_each_record_once():
    mono = Mono()
    pool, records, _ = make_pool(lambda: Session(mono, 0.5), mono)

    async def run():
        await pool.acquire_connection(IP)
    asyncio.run(run())
    assert [(r[1], r[2], r[3]) for r in records] == [(COMMAND_CONNECT, 500, True)]

    pool2, records2, _ = make_pool(lambda: Session(mono, 0.25, exc=RuntimeError("x")), mono)
    with pytest.raises(RuntimeError):
        asyncio.run(pool2.acquire_connection(IP))
    assert [(r[1], r[2], r[3], r[4]) for r in records2] == [(COMMAND_CONNECT, 250, False, "error")]


def test_refused_and_other_oserror_reasons():
    for exc, want in [(ConnectionRefusedError(), "connection_refused"), (OSError("no route"), "unreachable")]:
        pool, records, _ = make_pool(lambda exc=exc: Session(exc=exc))
        with pytest.raises(OSError):
            asyncio.run(pool.acquire_connection(IP))
        assert records[0][4] == want


def test_jetkvm_failures_are_classified():
    for exc, want in [(KVMLoginFailed("登入失敗 401"), "login_failed"), (KVMRpcNotOpen("x"), "rpc_not_open"),
                      (KVMNoSignal("x"), "no_signal")]:
        assert isinstance(exc, RuntimeError)
        assert classify_connect_error(exc) == want
        pool, records, _ = make_pool(lambda exc=exc: Session(exc=exc))
        with pytest.raises(RuntimeError):
            asyncio.run(pool.acquire_connection(IP))
        assert records[0][4] == want


def test_classifier_other_cases():
    assert classify_connect_error(ValueError("x")) == "error"
    assert classify_connect_error(asyncio.CancelledError()) == "cancelled"
    assert classify_connect_error(asyncio.TimeoutError()) == "timeout"

    class ReadTimeout(Exception):
        pass
    assert classify_connect_error(ReadTimeout()) == "timeout"
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = KVMLoginFailed("inner")
    assert classify_connect_error(wrapped) == "login_failed"
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__context__, b.__context__ = b, a  # cycle must terminate
    assert classify_connect_error(a) == "error"


def test_unknown_error_logs_class_name(caplog):
    class Weird(Exception):
        pass
    rec = TimingRecord(1.0, "DFU-01", IP, COMMAND_CONNECT, 5, False, "error", error_type=type(Weird()).__name__)
    with caplog.at_level(logging.INFO, logger="KVMTiming"):
        log_timing(rec)
    assert "error=Weird" in caplog.records[0].getMessage()


def test_cancelled_while_connecting_records_and_propagates():
    pool, records, _ = make_pool(lambda: Session(hang=True))

    async def run():
        task = asyncio.ensure_future(pool.acquire_connection(IP))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert len(records) == 1
    assert (records[0][1], records[0][3], records[0][4]) == (COMMAND_CONNECT, False, "cancelled")


def test_reuse_and_skipped_background_retry_produce_no_record():
    pool, records, epoch = make_pool(lambda: Session())

    async def run():
        await pool.acquire_connection(IP)
        await pool.release_connection(IP)
        assert len(records) == 1
        await pool.acquire_connection(IP)  # reuse
        # background retry that is due but finds the IP already connected
        pool.connect_failures.record_failure(IP)
        due(pool, epoch)
        await pool._get_connection(IP, "", lease=False, connect_timeout=1.0)
    asyncio.run(run())
    assert len(records) == 1


def test_no_observer_is_a_noop():
    pool = KVMConnectionPool(client_factory=lambda host, password="": Session())
    assert pool.timing_observer is None
    asyncio.run(pool.acquire_connection(IP))


def test_observer_exception_does_not_break_pool():
    def boom(*a):
        raise RuntimeError("observer")
    pool = KVMConnectionPool(client_factory=lambda host, password="": Session(), timing_observer=boom)
    asyncio.run(pool.acquire_connection(IP))
    assert IP in pool._pool


def fmt(record) -> str:
    return BilingualFormatter().format(record)


def test_log_line_format_and_levels(caplog):
    ok = TimingRecord(1.0, "DFU-03", "192.168.1.10", BACKGROUND_RECONNECT, 1240, True)
    bad = TimingRecord(1.0, "DFU-03", "192.168.1.10", BACKGROUND_RECONNECT, 10000, False, "timeout",
                       error_type="TimeoutError")
    anon = TimingRecord(1.0, "", "192.168.1.10", COMMAND_CONNECT, 7, True)
    with caplog.at_level(logging.INFO, logger="KVMTiming"):
        log_timing(ok)
        log_timing(bad)
        log_timing(anon)
    r_ok, r_bad, r_anon = caplog.records
    assert r_ok.levelno == logging.INFO and r_bad.levelno == logging.WARNING
    assert fmt(r_ok)[9:] == ("| KVM timing device=DFU-03 ip=192.168.1.10 path=background_reconnect result=ok "
                             "elapsed_ms=1240 | KVM 計時 設備=DFU-03 IP=192.168.1.10 路徑=背景重連 結果=成功 耗時=1240 ms")
    assert fmt(r_bad)[9:] == (
        "| KVM timing device=DFU-03 ip=192.168.1.10 path=background_reconnect result=fail elapsed_ms=10000 "
        "reason=timeout error=TimeoutError | KVM 計時 設備=DFU-03 IP=192.168.1.10 路徑=背景重連 結果=失敗 "
        "耗時=10000 ms 原因=逾時 例外=TimeoutError")
    assert "device= ip=" in fmt(r_anon) and "設備= IP=" in fmt(r_anon)
    assert "路徑=指令連線" in fmt(r_anon)


def test_percent_in_text_cannot_break_the_line(caplog):
    rec = TimingRecord(1.0, "D%s-01", IP, COMMAND_CONNECT, 1, False, "error", error_type="E%d")
    with caplog.at_level(logging.INFO, logger="KVMTiming"):
        log_timing(rec)
    assert "device=D%s-01" in fmt(caplog.records[0])


def make_server(registry=None):
    registry = registry or make_registry()
    pool = KVMConnectionPool(client_factory=lambda host, password="": Session())
    server = TcpJsonServer(device_registry=registry, kvm_pool=pool, host="127.0.0.1", port=0)
    return server, pool, registry


def test_server_observer_resolves_device_and_unknown_ip(caplog):
    server, pool, registry = make_server()
    entry = registry.entries()[0]
    with caplog.at_level(logging.INFO, logger="KVMTiming"):
        pool.timing_observer(entry.kvm_ip, COMMAND_CONNECT, 12, True, "", None)
        pool.timing_observer("10.9.9.9", COMMAND_CONNECT, 13, False, "timeout", TimeoutError())
    known, unknown = (fmt(r) for r in caplog.records)
    assert f"device={entry.device_id} ip={entry.kvm_ip}" in known
    assert "device= ip=10.9.9.9" in unknown and "error=TimeoutError" in unknown


def test_server_new_connection_over_tcp_logs_device_and_ip(caplog):
    registry = make_registry()
    entry = registry.get("DFU-01")
    pool = KVMConnectionPool(client_factory=lambda host, password="": Session())
    server = TcpJsonServer(device_registry=registry, kvm_pool=pool, host="127.0.0.1", port=0)

    server.start()
    try:
        with caplog.at_level(logging.INFO, logger="KVMTiming"):
            with socket.create_connection(server.server_address, timeout=5) as s:
                s.sendall(dumps_request(build_request("start_test", "r1", "DFU-01")))
                recv_reply(s)
    finally:
        server.stop()
        server.wait(5)
    lines = [fmt(r) for r in caplog.records if r.name == "KVMTiming"]
    assert len(lines) == 1
    assert f"device=DFU-01 ip={entry.kvm_ip} path=command_connect result=ok" in lines[0]
