"""Tests for KVM Connection Pool with Heartbeat & Idle Eviction (Ticket 03).

Verifies:
- Consecutive requests targeting the same KVM IP reuse the active connection without re-signaling.
- Connections to different KVM IPs are managed independently in the pool.
- Background heartbeat periodically pulls frames from active connections to prevent timeouts.
- Detected connection failures or disconnected states during heartbeat cleanly evict the connection.
- Connections idle for longer than the idle timeout (default 10 min) are cleanly evicted and closed.
- Subsequent requests to an evicted KVM transparently re-establish a fresh connection.
- Concurrent connection requests to the same IP are deduplicated.
- Integration with TcpJsonServer over newline-delimited TCP JSON protocol.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from kvm_pool import KVMConnectionPool, normalize_kvm_ip
from tcp_server import TcpJsonServer


class MockJetKVMClient:
    """Mock WebRTC KVM client simulating JetKVMClient interface."""

    def __init__(self, host: str, password: str = "", should_fail_connect: bool = False) -> None:
        self.host = host
        self.password = password
        self.should_fail_connect = should_fail_connect
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0
        self.latest_frame_calls = 0
        self.frame_sequence = 0
        self.latest_frame_exc: Optional[Exception] = None

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.should_fail_connect:
            raise ConnectionError(f"Simulated connection failure to {self.host}")
        await asyncio.sleep(0.01)
        self.connected = True

    async def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    def latest_frame(self) -> Optional[tuple[Any, int, float, str, float]]:
        self.latest_frame_calls += 1
        if self.latest_frame_exc is not None:
            raise self.latest_frame_exc
        if not self.connected:
            return None
        self.frame_sequence += 1
        return ("dummy_frame", self.frame_sequence, time.monotonic(), "mock_stream", 1000.0 + self.frame_sequence)


class MockClientFactory:
    """Factory tracking created MockJetKVMClient instances."""

    def __init__(self) -> None:
        self.created_clients: Dict[str, List[MockJetKVMClient]] = {}
        self.fail_hosts: set[str] = set()

    def __call__(self, host: str, password: str = "") -> MockJetKVMClient:
        client = MockJetKVMClient(
            host=host,
            password=password,
            should_fail_connect=(host in self.fail_hosts),
        )
        self.created_clients.setdefault(host, []).append(client)
        return client

    def get_latest_client(self, host: str) -> Optional[MockJetKVMClient]:
        clients = self.created_clients.get(host, [])
        return clients[-1] if clients else None


# ---------------------------------------------------------------------------
# Unit Tests for KVMConnectionPool
# ---------------------------------------------------------------------------

def test_normalize_kvm_ip() -> None:
    """Verify IP / host normalization handles URLs, whitespace, ports, and trailing slashes."""
    assert normalize_kvm_ip("192.168.1.50") == "192.168.1.50"
    assert normalize_kvm_ip("  192.168.1.50  ") == "192.168.1.50"
    assert normalize_kvm_ip("http://192.168.1.50") == "192.168.1.50"
    assert normalize_kvm_ip("http://192.168.1.50/") == "192.168.1.50"
    assert normalize_kvm_ip("https://192.168.1.50:8080/") == "192.168.1.50:8080"
    assert normalize_kvm_ip("ws://192.168.1.50") == "192.168.1.50"
    assert normalize_kvm_ip("wss://192.168.1.50") == "192.168.1.50"

    with pytest.raises(ValueError):
        normalize_kvm_ip("")
    with pytest.raises(ValueError):
        normalize_kvm_ip("   ")
    with pytest.raises(ValueError):
        normalize_kvm_ip(None)


def test_connection_pooling_and_reuse() -> None:
    """Consecutive requests to the same KVM IP reuse the active connection without re-connecting."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory)

        # First request connects
        client1 = await pool.get_connection("192.168.1.10")
        assert client1.connected is True
        assert client1.connect_calls == 1
        assert pool.active_count() == 1
        assert pool.get_active_ips() == ["192.168.1.10"]

        # Second request reuses existing connection
        client2 = await pool.get_connection("192.168.1.10")
        assert client2 is client1
        assert client2.connect_calls == 1  # No second connect() call
        assert pool.active_count() == 1

        # Normalization check: "http://192.168.1.10/" maps to the same entry
        client3 = await pool.get_connection("http://192.168.1.10/")
        assert client3 is client1
        assert client3.connect_calls == 1

        await pool.close_all()

    asyncio.run(_test())


def test_multiple_distinct_kvm_ips() -> None:
    """Requests targeting different KVM IPs maintain separate connections in the pool."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory)

        c1 = await pool.get_connection("192.168.1.10")
        c2 = await pool.get_connection("192.168.1.20")

        assert c1 is not c2
        assert c1.connected is True
        assert c2.connected is True
        assert pool.active_count() == 2
        assert sorted(pool.get_active_ips()) == ["192.168.1.10", "192.168.1.20"]

        await pool.close_all()
        assert pool.active_count() == 0
        assert c1.connected is False
        assert c2.connected is False

    asyncio.run(_test())


def test_background_heartbeat_pulls_frames() -> None:
    """Heartbeat pulls frames from all open connections and updates stats."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory, heartbeat_interval=0.05)

        client = await pool.get_connection("192.168.1.10")
        assert client.latest_frame_calls == 0

        # Perform single heartbeat pass
        count = await pool.perform_heartbeat()
        assert count == 1
        assert client.latest_frame_calls == 1

        info = pool.get_connection_info("192.168.1.10")
        assert info is not None
        assert info["heartbeat_count"] == 1
        assert info["last_heartbeat_at"] > 0

        # Run background worker for a brief moment
        await pool.start()
        assert pool.is_running is True
        await asyncio.sleep(0.12)
        await pool.stop()
        assert pool.is_running is False

        assert client.latest_frame_calls >= 2
        await pool.close_all()

    asyncio.run(_test())


def test_heartbeat_detects_disconnect_and_evicts() -> None:
    """Heartbeat detects broken or errored connections and cleanly evicts them."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory)

        client = await pool.get_connection("192.168.1.10")
        assert pool.active_count() == 1

        # Simulate client error during frame pull
        client.latest_frame_exc = ConnectionResetError("Remote KVM closed connection")

        count = await pool.perform_heartbeat()
        assert count == 0
        # Connection should have been evicted
        assert pool.active_count() == 0
        assert pool.get_active_ips() == []
        assert client.close_calls >= 1

        await pool.close_all()

    asyncio.run(_test())


def test_idle_eviction_after_timeout() -> None:
    """Connections idle for longer than idle_timeout are evicted and closed."""
    async def _test() -> None:
        factory = MockClientFactory()
        # Configure short idle timeout for test
        pool = KVMConnectionPool(client_factory=factory, idle_timeout=0.2)

        c1 = await pool.get_connection("192.168.1.10")
        assert pool.active_count() == 1

        # Immediately check eviction: should not evict yet
        evicted = await pool.prune_idle_connections()
        assert evicted == 0
        assert pool.active_count() == 1

        # Wait for idle timeout to expire
        await asyncio.sleep(0.25)

        evicted = await pool.prune_idle_connections()
        assert evicted == 1
        assert pool.active_count() == 0
        assert c1.close_calls == 1
        assert c1.connected is False

        await pool.close_all()

    asyncio.run(_test())


def test_idle_eviction_preserves_active_connection() -> None:
    """Connections that are regularly used are not evicted."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory, idle_timeout=0.3)

        c1 = await pool.get_connection("192.168.1.10")

        # Use connection after 0.15s
        await asyncio.sleep(0.15)
        await pool.get_connection("192.168.1.10")

        # Check after another 0.18s (total 0.33s from start, but only 0.18s since last use)
        await asyncio.sleep(0.18)
        evicted = await pool.prune_idle_connections()
        assert evicted == 0
        assert pool.active_count() == 1

        await pool.close_all()

    asyncio.run(_test())


def test_transparent_reestablishment_after_eviction() -> None:
    """Subsequent request to an evicted KVM transparently connects a fresh instance."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory, idle_timeout=0.1)

        c1 = await pool.get_connection("192.168.1.10")
        assert c1.connect_calls == 1

        await asyncio.sleep(0.15)
        await pool.prune_idle_connections()
        assert pool.active_count() == 0
        assert c1.connected is False

        # Next request transparently creates new connection
        c2 = await pool.get_connection("192.168.1.10")
        assert c2 is not c1
        assert c2.connected is True
        assert c2.connect_calls == 1
        assert pool.active_count() == 1

        await pool.close_all()

    asyncio.run(_test())


def test_concurrent_get_connection_deduplication() -> None:
    """Concurrent requests targeting the same KVM IP only trigger one connection attempt."""
    async def _test() -> None:
        factory = MockClientFactory()
        pool = KVMConnectionPool(client_factory=factory)

        results = await asyncio.gather(
            pool.get_connection("192.168.1.50"),
            pool.get_connection("192.168.1.50"),
            pool.get_connection("192.168.1.50"),
        )

        assert len(results) == 3
        # All three requests must have received the identical client instance
        assert results[0] is results[1]
        assert results[1] is results[2]
        assert results[0].connect_calls == 1
        assert pool.active_count() == 1

        await pool.close_all()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# Integration Tests with TcpJsonServer
# ---------------------------------------------------------------------------

_SOCK_BUFFERS: dict[int, bytearray] = {}


def send_json(sock: socket.socket, obj: dict) -> None:
    line = json.dumps(obj) + "\n"
    sock.sendall(line.encode("utf-8"))


def recv_json(sock: socket.socket, timeout: float = 3.0) -> dict:
    sock.settimeout(timeout)
    buf = _SOCK_BUFFERS.setdefault(id(sock), bytearray())
    start = time.time()
    while True:
        if b"\n" in buf:
            line, _, rest = buf.partition(b"\n")
            buf.clear()
            buf.extend(rest)
            return json.loads(line.decode("utf-8"))
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            raise TimeoutError(f"recv_json timed out after {timeout}s")
        if not chunk:
            raise ConnectionError("Socket closed while reading response")
        buf.extend(chunk)
        if time.time() - start > timeout:
            raise TimeoutError("recv_json exceeded timeout")


def test_tcp_server_kvm_pool_reuse_and_status() -> None:
    """TCP server reuses KVM connections for consecutive requests and exposes pool status."""
    factory = MockClientFactory()
    pool = KVMConnectionPool(client_factory=factory)
    server = TcpJsonServer(host="127.0.0.1", port=0, kvm_pool=pool)
    server.start()

    try:
        host, port = server.server_address
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        # 1. Request with kvm_ip
        send_json(sock, {"req_id": "r1", "cmd": "ping", "kvm_ip": "192.168.1.10"})
        resp1 = recv_json(sock)
        assert resp1["req_id"] == "r1"
        assert resp1["status"] == "ok"

        client1 = factory.get_latest_client("192.168.1.10")
        assert client1 is not None
        assert client1.connect_calls == 1

        # 2. Sequential request targeting same KVM IP
        send_json(sock, {"req_id": "r2", "cmd": "ping", "kvm_ip": "192.168.1.10"})
        resp2 = recv_json(sock)
        assert resp2["req_id"] == "r2"
        assert resp2["status"] == "ok"
        # Must not reconnect
        assert client1.connect_calls == 1

        # 3. Check status reporting
        send_json(sock, {"req_id": "r3", "cmd": "status"})
        resp3 = recv_json(sock)
        assert resp3["status"] == "ok"
        kvm_status = resp3["data"]["kvm_pool"]
        assert kvm_status["active_connections"] == 1
        assert kvm_status["cached_ips"] == ["192.168.1.10"]

        sock.close()
    finally:
        server.stop()
        server.wait(timeout=2.0)


def test_tcp_server_kvm_connection_failure_handling() -> None:
    """TCP server gracefully returns error envelope when KVM connection fails."""
    factory = MockClientFactory()
    factory.fail_hosts.add("192.168.1.99")
    pool = KVMConnectionPool(client_factory=factory)
    server = TcpJsonServer(host="127.0.0.1", port=0, kvm_pool=pool)
    server.start()

    try:
        host, port = server.server_address
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        send_json(sock, {
            "req_id": "r-fail",
            "cmd": "ping",
            "device": "1",
            "kvm_ip": "192.168.1.99",
        })
        resp = recv_json(sock)
        assert resp["req_id"] == "r-fail"
        assert resp["status"] == "error"
        assert resp["error"] == "kvm_connection_failed"
        assert "192.168.1.99" in resp["message"]

        # Ensure device lock was released despite KVM failure
        assert not server.lock_manager.is_locked("1")

        sock.close()
    finally:
        server.stop()
        server.wait(timeout=2.0)


def test_tcp_server_idle_eviction_and_reconnection() -> None:
    """TCP server cleanly handles idle eviction and subsequent re-connection."""
    factory = MockClientFactory()
    pool = KVMConnectionPool(client_factory=factory, idle_timeout=0.15, heartbeat_interval=0.05)
    server = TcpJsonServer(host="127.0.0.1", port=0, kvm_pool=pool)
    server.start()

    try:
        host, port = server.server_address
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        # First request connects
        send_json(sock, {"req_id": "r1", "cmd": "ping", "kvm_ip": "192.168.1.30"})
        resp1 = recv_json(sock)
        assert resp1["status"] == "ok"
        c1 = factory.get_latest_client("192.168.1.30")
        assert c1 is not None
        assert c1.connected is True

        # Wait for idle timeout and background eviction
        time.sleep(0.3)
        assert c1.connected is False
        assert pool.active_count() == 0

        # Subsequent request transparently re-establishes connection
        send_json(sock, {"req_id": "r2", "cmd": "ping", "kvm_ip": "192.168.1.30"})
        resp2 = recv_json(sock)
        assert resp2["status"] == "ok"
        c2 = factory.get_latest_client("192.168.1.30")
        assert c2 is not None
        assert c2 is not c1
        assert c2.connected is True

        sock.close()
    finally:
        server.stop()
        server.wait(timeout=2.0)
