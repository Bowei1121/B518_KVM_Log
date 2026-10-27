"""core_status and LabVIEW status carry the connection failure fields from the same DeviceHealth (issue #134)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from connect_failures import ConnectFailures  # noqa: E402
from device_health import DeviceHealth  # noqa: E402
from kvm_pool import KVMConnectionPool  # noqa: E402
from protocol_helpers import make_registry  # noqa: E402
from tcp_server import CommandContext, TcpJsonServer  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def make_server(**kw):
    clock = Clock()
    tracker = ConnectFailures(clock=clock)
    pool = KVMConnectionPool(connect_failures=tracker)
    registry = make_registry()
    server = TcpJsonServer(device_registry=registry, kvm_pool=pool, host="127.0.0.1", port=0, **kw)
    return server, registry, tracker, clock


def fail(tracker, clock, ip, times):
    for _ in range(times):
        tracker.record_failure(ip)
        clock.now += 1


def test_device_health_defaults_for_connect_fields():
    h = DeviceHealth(device="D", station="DFU", kvm_ip="1.2.3.4", connected=False, busy=False, frozen=False)
    assert (h.connect_fail_count, h.connect_failed, h.retrying) == (0, False, False)
    assert (h.retry_in_sec, h.first_fail_at, h.last_connect_error) == (None, None, None)


def test_core_status_and_status_agree_for_a_failing_and_a_healthy_device():
    server, registry, tracker, clock = make_server()
    bad, good = registry.entries()[0], registry.entries()[1]
    fail(tracker, clock, bad.kvm_ip, 3)
    tracker.record_failure(good.kvm_ip)  # one failure, no alarm

    rows = {r["device"]: r for r in server._station_rows()}
    status = asyncio.run(server._handle_status(CommandContext(), None))
    devices = {d["device"]: d for d in status["devices"]}

    assert rows[bad.device_id]["connect_fail_count"] == devices[bad.device_id]["connect_fail_count"] == 3
    assert rows[bad.device_id]["connect_failed"] is devices[bad.device_id]["connect_failed"] is True
    assert rows[good.device_id]["connect_fail_count"] == devices[good.device_id]["connect_fail_count"] == 1
    assert rows[good.device_id]["connect_failed"] is devices[good.device_id]["connect_failed"] is False
    other = registry.entries()[2].device_id
    assert rows[other]["connect_fail_count"] == devices[other]["connect_fail_count"] == 0


def test_station_rows_append_the_new_keys_after_the_existing_ones():
    server, registry, tracker, clock = make_server()
    fail(tracker, clock, registry.entries()[0].kvm_ip, 2)
    row = server._station_rows()[0]
    keys = list(row)
    assert keys[:7] == ["station", "device", "kvm_ip", "status", "is_busy", "is_frozen", "is_connected"]
    assert keys[-7:] == ["connect_fail_count", "connect_failed", "retrying", "retry_in_sec", "retry_threshold",
                         "first_fail_at", "last_connect_error"]
    assert row["connect_fail_count"] == 2
    assert row["retrying"] is False
    assert row["retry_threshold"] == 3
    assert row["first_fail_at"] == 5000.0
    assert row["last_connect_error"] == "kvm_connect_failed"
    assert type(row["retry_in_sec"]) is int


def test_station_row_without_failures_has_null_extras():
    server, _, _, _ = make_server()
    row = server._station_rows()[0]
    assert (row["connect_fail_count"], row["connect_failed"], row["retrying"]) == (0, False, False)
    assert (row["retry_in_sec"], row["first_fail_at"], row["last_connect_error"]) == (None, None, None)
    assert row["retry_threshold"] == 3


def test_retry_in_sec_counts_down_and_retrying_hides_it():
    server, registry, tracker, clock = make_server()
    ip = registry.entries()[0].kvm_ip
    tracker.record_failure(ip)
    assert server._station_rows()[0]["retry_in_sec"] == 10
    clock.now += 4
    assert server._station_rows()[0]["retry_in_sec"] == 6
    tracker.set_retrying(ip, True)
    row = server._station_rows()[0]
    assert (row["retrying"], row["retry_in_sec"]) == (True, None)


def test_status_devices_always_carry_both_fields_never_null_and_no_ip():
    server, registry, tracker, clock = make_server()
    fail(tracker, clock, registry.entries()[0].kvm_ip, 3)
    status = asyncio.run(server._handle_status(CommandContext(), None))
    assert len(status["devices"]) == len(registry.entries())
    for dev in status["devices"]:
        assert list(dev)[:6] == ["device", "station", "connected", "busy", "frozen", "channels"]
        assert list(dev)[6:] == ["connect_failed", "connect_fail_count"]
        assert type(dev["connect_failed"]) is bool
        assert type(dev["connect_fail_count"]) is int
    blob = json.dumps(status)
    assert "kvm_ip" not in blob
    for entry in registry.entries():
        assert entry.kvm_ip not in blob


def test_kvm_ip_is_matched_after_normalisation():
    server, registry, tracker, clock = make_server()
    first = registry.entries()[0]
    server._health[first.device_id].kvm_ip = f"http://{first.kvm_ip}/"
    fail(tracker, clock, first.kvm_ip, 1)
    row = server._station_rows()[0]
    assert row["connect_fail_count"] == 1


def test_success_clears_both_endpoints():
    server, registry, tracker, clock = make_server()
    ip = registry.entries()[0].kvm_ip
    fail(tracker, clock, ip, 3)
    tracker.record_success(ip)
    row = server._station_rows()[0]
    dev = asyncio.run(server._handle_status(CommandContext(), None))["devices"][0]
    assert (row["connect_failed"], row["connect_fail_count"]) == (False, 0)
    assert (dev["connect_failed"], dev["connect_fail_count"]) == (False, 0)


def test_core_status_data_has_the_stations_with_new_fields():
    server, registry, tracker, clock = make_server()
    fail(tracker, clock, registry.entries()[0].kvm_ip, 3)
    data = asyncio.run(server._handle_core_status(CommandContext(), None))
    assert data["stations"][0]["connect_failed"] is True


def test_defaults_are_3_60_10_10():
    server = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0)
    tracker = server.kvm_pool.connect_failures
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (3, 60.0, 10.0)
    assert server.kvm_pool.connect_timeout_sec == 10.0
    assert server._station_rows()[0]["retry_threshold"] == 3


def test_constructor_parameters_reach_the_default_pool_tracker():
    server = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0,
                           connect_fail_threshold=2, connect_fail_window_sec=30.0,
                           connect_retry_interval_sec=5.0, connect_timeout_sec=4.0)
    tracker = server.kvm_pool.connect_failures
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (2, 30.0, 5.0)
    assert server.kvm_pool.connect_timeout_sec == 4.0
    assert server._station_rows()[0]["retry_threshold"] == 2


def test_constructor_parameters_configure_an_injected_pool():
    pool = KVMConnectionPool()
    TcpJsonServer(device_registry=make_registry(), kvm_pool=pool, host="127.0.0.1", port=0,
                  connect_fail_threshold=5, connect_fail_window_sec=90.0,
                  connect_retry_interval_sec=7.0, connect_timeout_sec=3.0)
    tracker = pool.connect_failures
    assert (tracker.threshold, tracker.window_sec, tracker.retry_interval_sec) == (5, 90.0, 7.0)
    assert pool.connect_timeout_sec == 3.0


def test_a_pool_without_a_tracker_still_gives_quiet_defaults():
    class BarePool:
        def get_active_ips(self):
            return []

        def active_count(self):
            return 0

    server = TcpJsonServer(device_registry=make_registry(), kvm_pool=BarePool(), host="127.0.0.1", port=0)
    row = server._station_rows()[0]
    assert (row["connect_fail_count"], row["connect_failed"], row["retry_threshold"]) == (0, False, 3)
