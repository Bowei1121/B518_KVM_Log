"""Heartbeat timing records and lost/recovered events in the Core log (issue #157, spec #154)."""
import asyncio
import logging
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bilingual_log import BilingualFormatter  # noqa: E402
from core_protocol import build_request, dumps_request  # noqa: E402
from fake_kvm import FakeKVM, FakeKVMSession  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry, recv_reply  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

REGISTRY = make_registry()
IP = REGISTRY.get("DFU-03").kvm_ip
IP2 = REGISTRY.get("DFU-04").kvm_ip


class Mono:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Rig:
    """A pool with injected clock, the sessions it created, and what its observer saw.

    The server is built (not started) so its observer writes the Core log lines.
    """

    def __init__(self):
        self.mono = Mono()
        self.sessions = {}
        self.calls = []
        self.pool = KVMConnectionPool(client_factory=self._make, monotonic=self.mono)
        self.server = TcpJsonServer(device_registry=REGISTRY, kvm_pool=self.pool, host="127.0.0.1", port=0)
        log_observer = self.pool.timing_observer

        def observe(*a):
            self.calls.append(a)
            log_observer(*a)
        self.pool.timing_observer = observe

    def _make(self, host, password=""):
        s = FakeKVM()
        self.sessions[host] = s
        return s

    async def connect(self, *ips):
        for ip in ips:
            await self.pool.acquire_connection(ip)
            await self.pool.release_connection(ip)

    def beats(self):
        return [c for c in self.calls if c[1] == "heartbeat"]

    def events(self):
        return [c[6] for c in self.beats() if c[6] is not None]


def fmt(record) -> str:
    return BilingualFormatter().format(record)


def hb_lines(caplog):
    return [r for r in caplog.records if "path=heartbeat" in r.getMessage()]


def test_two_healthy_connections_log_two_ok_lines(caplog):
    rig = Rig()

    async def go():
        await rig.connect(IP, IP2)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="KVMTiming"):
            assert await rig.pool.perform_heartbeat() == 2
    asyncio.run(go())
    recs = hb_lines(caplog)
    assert len(recs) == 2
    assert all(r.levelno == logging.INFO and "result=ok" in r.getMessage() for r in recs)
    assert f"device=DFU-03 ip={IP} path=heartbeat result=ok elapsed_ms=0" in fmt(recs[0])
    assert "路徑=心跳 結果=成功" in fmt(recs[0])
    assert rig.events() == []


def _fail_case(prepare, reason, caplog):
    rig = Rig()

    async def go():
        await rig.connect(IP)
        prepare(rig.sessions[IP])
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="KVMTiming"):
            await rig.pool.perform_heartbeat()
    asyncio.run(go())
    recs = hb_lines(caplog)
    assert len(recs) == 1 and recs[0].levelno == logging.WARNING
    assert f"result=fail elapsed_ms=0 reason={reason}" in recs[0].getMessage()


def test_failure_reasons(caplog):
    _fail_case(lambda s: s.end_track(), "track_ended", caplog)
    assert "結果=失敗 耗時=0 ms 原因=影像中斷" in fmt(hb_lines(caplog)[0])
    _fail_case(lambda s: s.set_peer_state("failed"), "peer_failed", caplog)
    _fail_case(lambda s: s.set_peer_state("closed"), "peer_closed", caplog)
    _fail_case(lambda s: setattr(s, "connected", False), "not_connected", caplog)


def test_reason_check_order():
    s = FakeKVMSession()
    assert s.unhealthy_reason() == "not_connected"
    asyncio.run(s.connect())
    assert s.unhealthy_reason() == ""
    s.end_track()
    s.set_peer_state("failed")
    assert s.unhealthy_reason() == "track_ended"


def test_healthy_to_unhealthy_emits_one_lost():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert [(e.kind, e.ip, e.reason) for e in rig.events()] == [("heartbeat_lost", IP, "track_ended")]


def test_in_use_deferred_eviction_three_failures_one_lost(caplog):
    rig = Rig()

    async def go():
        await rig.pool.acquire_connection(IP)  # held, so eviction is deferred
        rig.sessions[IP].end_track()
        with caplog.at_level(logging.INFO, logger="KVMTiming"):
            for _ in range(3):
                rig.mono.now += 5
                await rig.pool.perform_heartbeat()
        assert IP in rig.pool.get_active_ips()  # eviction deferred while in use
    asyncio.run(go())
    lines = hb_lines(caplog)
    assert len(lines) == 3 and all(r.levelno == logging.WARNING for r in lines)
    assert len([r for r in caplog.records if "heartbeat_lost" in r.getMessage()]) == 1
    assert len(rig.events()) == 1


def test_recovery_outage_and_new_lost_after_recovery():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()  # evicted
        rig.mono.now += 75
        await rig.connect(IP)  # new, healthy connection
        await rig.pool.perform_heartbeat()
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert [(e.kind, e.outage_sec) for e in rig.events()] == [
        ("heartbeat_lost", None), ("heartbeat_recovered", 75), ("heartbeat_lost", None)]


def test_outage_rounds_half_up():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
        rig.mono.now += 74.5
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert rig.events()[-1].outage_sec == 75


def test_new_pool_has_no_leftover_state():
    old = Rig()

    async def prime():
        await old.connect(IP)
        old.sessions[IP].end_track()
        await old.pool.perform_heartbeat()
    asyncio.run(prime())
    assert [e.kind for e in old.events()] == ["heartbeat_lost"]

    healthy = Rig()

    async def ok_beat():
        await healthy.connect(IP)
        await healthy.pool.perform_heartbeat()
    asyncio.run(ok_beat())
    assert healthy.events() == []

    failing = Rig()

    async def fail_beat():
        await failing.connect(IP)
        failing.sessions[IP].end_track()
        await failing.pool.perform_heartbeat()
    asyncio.run(fail_beat())
    assert [e.kind for e in failing.events()] == ["heartbeat_lost"]


def test_lost_carries_since_ok_and_elapsed_is_measured():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()
        rig.mono.now += 5
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    lost = rig.events()[0]
    assert (lost.since_ok_sec, lost.reason) == (5, "track_ended")
    fail = rig.beats()[-1]
    assert fail[2] == 0 and fail[3] is False and fail[4] == "track_ended"


def test_since_ok_without_prior_ok_counts_from_pooling():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        rig.mono.now += 7
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert rig.events()[0].since_ok_sec == 7


def test_since_ok_counts_from_new_connection_not_an_older_one():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()  # OK on the first connection
        rig.mono.now += 600
        await rig.pool.prune_idle_connections(max_idle=-1)  # idle eviction, not a heartbeat failure
        await rig.connect(IP)
        rig.mono.now += 3
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert [(e.kind, e.since_ok_sec) for e in rig.events()] == [("heartbeat_lost", 3)]


def test_measured_elapsed_uses_injected_clock():
    rig = Rig()

    async def go():
        await rig.connect(IP)
        s = rig.sessions[IP]
        orig = s.is_healthy

        def slow():
            rig.mono.now += 0.25
            return orig()
        s.is_healthy = slow
        await rig.pool.perform_heartbeat()
    asyncio.run(go())
    assert rig.beats()[0][2] == 250


def test_observer_failure_does_not_break_heartbeat():
    rig = Rig()

    def boom(*a):
        raise RuntimeError("observer")

    async def go():
        await rig.connect(IP)
        rig.pool.timing_observer = boom
        return await rig.pool.perform_heartbeat()
    assert asyncio.run(go()) == 1


def test_server_fills_device_into_heartbeat_and_event_lines(caplog):
    rig = Rig()
    server = rig.server

    async def go():
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()
        rig.mono.now += 5
        rig.sessions[IP].end_track()
        await rig.pool.perform_heartbeat()
        rig.mono.now += 75
        await rig.connect(IP)
        await rig.pool.perform_heartbeat()

    server.start()
    try:
        with caplog.at_level(logging.INFO, logger="KVMTiming"):
            asyncio.run(go())
            with socket.create_connection(server.server_address, timeout=5) as s:  # server still serves
                s.sendall(dumps_request(build_request("start_test", "r1", "DFU-01")))
                recv_reply(s)
    finally:
        server.stop()
        server.wait(5)
    lines = [fmt(r) for r in caplog.records if r.name.startswith("KVMTiming")]
    lost = [x for x in lines if "heartbeat_lost" in x]
    rec = [x for x in lines if "heartbeat_recovered" in x]
    assert len(lost) == 1 and len(rec) == 1
    assert (f"KVM heartbeat_lost device=DFU-03 ip={IP} reason=track_ended since_ok_sec=5 | "
            f"KVM 心跳偵測不到 設備=DFU-03 IP={IP} 原因=影像中斷 距上次正常=5 秒") in lost[0]
    assert (f"KVM heartbeat_recovered device=DFU-03 ip={IP} outage_sec=75 | "
            f"KVM 心跳恢復 設備=DFU-03 IP={IP} 已中斷=75 秒") in rec[0]
    hb = [x for x in lines if "path=heartbeat" in x]
    assert len(hb) == 3 and all(f"device=DFU-03 ip={IP}" in x for x in hb)
    ev_recs = [r for r in caplog.records if "heartbeat_" in r.getMessage()]
    assert [r.levelno for r in ev_recs] == [logging.WARNING, logging.INFO]
