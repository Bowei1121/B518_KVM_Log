"""KVM Connection Pool counts connect failures and reconnects in the background (issue #134)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_kvm import FakeKVMSession  # noqa: E402
from kvm_pool import KVMConnectionPool, KVMUnavailableError  # noqa: E402

IP = "192.168.1.10"
OTHER = "192.168.1.11"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


class Client(FakeKVMSession):
    def __init__(self, host: str, mode: str, gate: asyncio.Event | None) -> None:
        self.host = host
        self.mode = mode
        self.gate = gate
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.mode == "fail":
            raise ConnectionError("simulated failure of 10.9.8.7")
        if self.mode == "hang":
            await asyncio.sleep(3600)
        if self.mode == "gate":
            assert self.gate is not None
            await self.gate.wait()
        await super().connect()

    async def close(self) -> None:
        self.close_calls += 1
        await super().close()


class Factory:
    def __init__(self) -> None:
        self.modes: Dict[str, str] = {}
        self.default_mode = "ok"
        self.gate = asyncio.Event()
        self.clients: List[Client] = []

    def __call__(self, host: str, password: str = "") -> Client:
        client = Client(host, self.modes.get(host, self.default_mode), self.gate)
        self.clients.append(client)
        return client

    def for_host(self, host: str) -> List[Client]:
        return [c for c in self.clients if c.host == host]


def make_pool(factory: Factory, clock: Clock, **kw) -> KVMConnectionPool:
    return KVMConnectionPool(client_factory=factory, clock=clock, **kw)


def run(coro):
    return asyncio.run(coro)


def test_failing_connect_counts_and_schedules_a_retry():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        state = pool.connect_failures.state(IP)
        assert (state.fail_count, state.connect_failed) == (1, False)
        assert state.next_retry_at == clock() + 10.0
        assert state.last_error == "kvm_connect_failed"
        assert pool.active_count() == 0
    run(go())


def test_http_prefixed_ip_is_counted_under_the_canonical_ip():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.acquire_connection(f"http://{IP}/")
        assert pool.connect_failures.state(IP).fail_count == 1
    run(go())


def test_successful_connect_resets_everything():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await pool.get_connection(IP)
            clock.advance(1)
        assert pool.connect_failures.state(IP).connect_failed is True
        factory.modes[IP] = "ok"
        await pool.get_connection(IP)
        state = pool.connect_failures.state(IP)
        assert (state.fail_count, state.connect_failed, state.last_error, state.first_fail_at) == (0, False, None, None)
        assert state.next_retry_at is None and state.retrying is False
        await pool.close_all()
    run(go())


def test_reusing_a_connection_does_not_count():
    async def go():
        factory, clock = Factory(), Clock()
        pool = make_pool(factory, clock)
        for _ in range(5):
            await pool.get_connection(IP)
        assert len(factory.for_host(IP)) == 1
        assert pool.connect_failures.state(IP).fail_count == 0
        await pool.close_all()
    run(go())


def test_idle_prune_does_not_count_and_schedules_no_retry():
    async def go():
        factory, clock = Factory(), Clock()
        pool = make_pool(factory, clock)
        await pool.get_connection(IP)
        assert await pool.prune_idle_connections(max_idle=-1) == 1
        assert pool.active_count() == 0
        assert pool.connect_failures.state(IP).fail_count == 0
        clock.advance(1000)
        await pool.retry_due_connections()
        assert len(factory.for_host(IP)) == 1  # no reconnect
    run(go())


def test_heartbeat_eviction_does_not_count_and_schedules_no_retry():
    async def go():
        factory, clock = Factory(), Clock()
        pool = make_pool(factory, clock)
        client = await pool.get_connection(IP)
        client.end_track()
        assert await pool.perform_heartbeat() == 0
        assert pool.active_count() == 0
        assert pool.connect_failures.state(IP).fail_count == 0
        clock.advance(1000)
        await pool.retry_due_connections()
        assert len(factory.for_host(IP)) == 1
    run(go())


def test_in_use_lost_path_does_not_count_and_schedules_no_retry():
    async def go():
        factory, clock = Factory(), Clock()
        pool = make_pool(factory, clock)
        client = await pool.acquire_connection(IP)
        client.end_track()
        with pytest.raises(KVMUnavailableError):
            await pool.acquire_connection(IP)
        assert pool.connect_failures.state(IP).fail_count == 0
        await pool.release_connection(IP)  # unhealthy at the last release -> evicted
        await pool.release_connection(IP)
        clock.advance(1000)
        await pool.retry_due_connections()
        assert len(factory.for_host(IP)) == 1
        assert pool.connect_failures.due(clock()) == []
    run(go())


def test_background_retry_counts_without_any_command_and_alarms_on_the_third():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)  # failure 1, the only command
        await pool.retry_due_connections()
        assert pool.connect_failures.state(IP).fail_count == 1  # 10 s have not passed
        clock.advance(9.9)
        await pool.retry_due_connections()
        assert pool.connect_failures.state(IP).fail_count == 1
        clock.advance(0.1)
        await pool.retry_due_connections()
        assert pool.connect_failures.state(IP).fail_count == 2
        assert pool.connect_failures.state(IP).connect_failed is False
        clock.advance(10)
        await pool.retry_due_connections()
        state = pool.connect_failures.state(IP)
        assert (state.fail_count, state.connect_failed) == (3, True)
        assert state.retrying is False
        assert len(factory.for_host(IP)) == 3
    run(go())


def test_keeps_retrying_every_10_seconds_after_the_alarm_and_failed_retries_never_clear_it():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        for expected in range(2, 9):
            clock.advance(10)
            await pool.retry_due_connections()
            state = pool.connect_failures.state(IP)
            assert state.fail_count == expected
            assert state.connect_failed is (expected >= 3)
    run(go())


def test_retry_success_clears_the_alarm_and_stops_retries():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        for _ in range(2):
            clock.advance(10)
            await pool.retry_due_connections()
        assert pool.connect_failures.state(IP).connect_failed is True
        factory.modes[IP] = "ok"
        clock.advance(10)
        await pool.retry_due_connections()
        state = pool.connect_failures.state(IP)
        assert (state.fail_count, state.connect_failed, state.retrying, state.next_retry_at) == (0, False, False, None)
        assert pool.is_connected(IP)
        calls = len(factory.for_host(IP))
        clock.advance(100)
        await pool.retry_due_connections()
        assert len(factory.for_host(IP)) == calls
        await pool.close_all()
    run(go())


def test_background_retry_is_unleased():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        factory.modes[IP] = "ok"
        clock.advance(10)
        await pool.retry_due_connections()
        assert pool.get_connection_info(IP)["in_use"] == 0
        await pool.close_all()
    run(go())


def test_hanging_connect_times_out_in_the_background_and_counts():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock, connect_timeout_sec=0.05)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        factory.modes[IP] = "hang"
        clock.advance(10)
        await asyncio.wait_for(pool.retry_due_connections(), timeout=2)
        state = pool.connect_failures.state(IP)
        assert state.fail_count == 2 and state.retrying is False
        hung = factory.for_host(IP)[-1]
        assert hung.close_calls == 1  # half-made client closed
        assert pool.active_count() == 0
    run(go())


def test_command_path_connect_has_no_timeout():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "gate"
        pool = make_pool(factory, clock, connect_timeout_sec=0.01)
        task = asyncio.ensure_future(pool.get_connection(IP))
        await asyncio.sleep(0.05)
        assert not task.done()
        factory.gate.set()
        await task
        assert pool.is_connected(IP)
        await pool.close_all()
    run(go())


def test_background_retry_shares_the_ip_lock_with_a_command():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        factory.modes[IP] = "gate"
        command = asyncio.ensure_future(pool.acquire_connection(IP))
        await asyncio.sleep(0.01)  # the command holds the ip lock inside connect()
        clock.advance(10)
        retry = asyncio.ensure_future(pool.retry_due_connections())
        await asyncio.sleep(0.01)
        assert not retry.done()  # waiting for the same lock
        assert len(factory.for_host(IP)) == 2
        factory.gate.set()
        await command
        await retry
        assert len(factory.for_host(IP)) == 2  # no second connection
        assert pool.get_connection_info(IP)["in_use"] == 1
        assert pool.connect_failures.state(IP).fail_count == 0
        await pool.close_all()
    run(go())


def test_ips_fail_independently_and_do_not_block_each_other():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        await pool.get_connection(OTHER)
        assert pool.connect_failures.state(OTHER).fail_count == 0
        assert pool.connect_failures.state(IP).fail_count == 1
        await pool.close_all()
    run(go())


def test_parameters_reach_the_tracker_and_change_behaviour():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock, connect_fail_threshold=2, connect_fail_window_sec=30.0,
                         connect_retry_interval_sec=5.0)
        tracker = pool.connect_failures
        assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (2, 30.0, 5.0)
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        clock.advance(5)
        await pool.retry_due_connections()
        assert tracker.state(IP).connect_failed is True
    run(go())


def test_defaults_are_3_60_10_10():
    pool = KVMConnectionPool()
    tracker = pool.connect_failures
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (3, 60.0, 10.0)
    assert pool.connect_timeout_sec == 10.0


def test_configure_connect_failures_updates_tracker_and_timeout():
    pool = KVMConnectionPool()
    pool.configure_connect_failures(threshold=2, window_sec=30.0, retry_interval_sec=5.0, connect_timeout_sec=4.0)
    tracker = pool.connect_failures
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (2, 30.0, 5.0)
    assert pool.connect_timeout_sec == 4.0


def test_worker_retries_by_itself_and_leaves_no_task_after_stop():
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock, retry_tick_sec=0.005)
        await pool.start()
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        clock.advance(10)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if pool.connect_failures.state(IP).fail_count >= 2:
                break
        assert pool.connect_failures.state(IP).fail_count >= 2
        await pool.stop()
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftover == []
    run(go())


@pytest.mark.parametrize("closer", ["stop", "close_all"])
def test_stop_and_close_all_cancel_the_worker_and_a_running_retry(closer):
    async def go():
        factory, clock = Factory(), Clock()
        factory.modes[IP] = "fail"
        pool = make_pool(factory, clock, retry_tick_sec=0.005, connect_timeout_sec=30.0)
        await pool.start()
        with pytest.raises(ConnectionError):
            await pool.get_connection(IP)
        factory.modes[IP] = "hang"
        clock.advance(10)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if pool.connect_failures.state(IP).retrying:
                break
        assert pool.connect_failures.state(IP).retrying is True
        await getattr(pool, closer)()
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftover == []
        assert pool.connect_failures.state(IP).retrying is False
        assert pool.is_running is False
    run(go())


def test_start_twice_keeps_a_single_retry_worker():
    async def go():
        pool = make_pool(Factory(), Clock(), retry_tick_sec=0.005)
        await pool.start()
        await pool.start()
        assert len(asyncio.all_tasks()) - 1 == 2  # maintenance + retry worker
        await pool.close_all()
        assert len(asyncio.all_tasks()) == 1
    run(go())
