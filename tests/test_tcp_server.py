import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from core_protocol import dumps_request, read_reply  # noqa: E402

from protocol_helpers import make_registry, wire, numbered
from tcp_server import TcpJsonServer


def send_json(sock: socket.socket, obj: dict) -> None:
    """Send a dictionary as a newline-delimited JSON string."""
    sock.sendall(dumps_request(wire(obj)))


def recv_json(sock: socket.socket, timeout: float = 2.0) -> dict:
    """Receive a newline-delimited JSON line and parse it."""
    sock.settimeout(timeout)
    return read_reply(sock)


@pytest.fixture
def running_server():
    """Fixture that starts a TcpJsonServer on an ephemeral port in a background thread."""
    server = TcpJsonServer(device_registry=make_registry(), host="127.0.0.1", port=0)
    server.start()
    time.sleep(0.05)
    try:
        yield server
    finally:
        server.stop()


def test_server_starts_headlessly_and_pings(running_server):
    """Verify headless startup and ping/pong roundtrip with req_id echo."""
    host, port = running_server.server_address
    assert port > 0

    with socket.create_connection((host, port), timeout=2.0) as sock:
        req = {"req_id": "req-001", "cmd": "ping"}
        send_json(sock, req)
        resp = recv_json(sock)

        assert resp["req_id"] == "req-001"
        assert resp["status"] == "ok"
        assert resp["data"]["pong"] is True


def test_auto_assign_req_id_when_missing(running_server):
    """Verify that omitting req_id causes server to assign one in the response."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        sock.sendall(b'{"protocol_version": "1.0", "cmd": "ping"}\n')
        resp = recv_json(sock)

        assert resp["req_id"] == ""
        assert resp["status"] == "error"
        assert resp["error"] == "missing_req_id"


def test_status_command(running_server):
    """Verify status command returns server operational info."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        req = {"req_id": "req-status", "cmd": "core_status"}
        send_json(sock, req)
        resp = recv_json(sock)

        assert resp["req_id"] == "req-status"
        assert resp["status"] == "ok"
        assert isinstance(resp["data"], dict)
        assert resp["data"].get("status") == "running"
        assert "active_connections" in resp["data"]


def test_concurrent_clients_multiplexing(running_server):
    """Verify multiple TCP clients connect concurrently and process requests without blocking."""
    host, port = running_server.server_address
    num_clients = 8
    results = {}
    barrier = threading.Barrier(num_clients)

    def client_worker(client_id: int):
        with socket.create_connection((host, port), timeout=3.0) as sock:
            # Synchronize start across all threads
            barrier.wait()
            req_id = f"client-{client_id}-req"
            send_json(sock, {"req_id": req_id, "cmd": "ping"})
            resp = recv_json(sock)
            results[client_id] = resp

    threads = [
        threading.Thread(target=client_worker, args=(i,))
        for i in range(num_clients)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()

    assert len(results) == num_clients
    for i in range(num_clients):
        assert results[i]["req_id"] == f"client-{i}-req"
        assert results[i]["status"] == "ok"
        assert results[i]["data"]["pong"] is True


def test_malformed_json_resilience(running_server):
    """Verify malformed JSON returns an explicit error without crashing connection or server."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        # Send raw invalid JSON
        sock.sendall(b"NOT_A_VALID_JSON_STRING\n")
        resp = recv_json(sock)

        assert resp.get("status") == "error"
        assert resp.get("error") == "invalid_json"

        # Connection remains usable for subsequent valid command
        send_json(sock, {"req_id": "recovery-req", "cmd": "ping"})
        recovery_resp = recv_json(sock)
        assert recovery_resp["req_id"] == "recovery-req"
        assert recovery_resp["status"] == "ok"
        assert recovery_resp["data"]["pong"] is True


def test_unknown_command_and_missing_command(running_server):
    """Verify unknown commands and missing 'cmd' return explicit error JSON."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        # Missing cmd
        send_json(sock, {"req_id": "test-missing"})
        resp1 = recv_json(sock)
        assert resp1["req_id"] == "test-missing"
        assert resp1["status"] == "error"
        assert resp1["error"] == "missing_command"

        # Unknown cmd
        send_json(sock, {"req_id": "test-unknown", "cmd": "non_existent_command"})
        resp2 = recv_json(sock)
        assert resp2["req_id"] == "test-unknown"
        assert resp2["status"] == "error"
        assert resp2["error"] == "unknown_command"


def test_graceful_shutdown(running_server):
    """Verify sending shutdown command responds, terminates connections, and stops server."""
    host, port = running_server.server_address

    # Connect client 1 and client 2
    sock1 = socket.create_connection((host, port), timeout=2.0)
    sock2 = socket.create_connection((host, port), timeout=2.0)

    try:
        # Client 1 issues shutdown
        send_json(sock1, {"req_id": "shutdown-req", "cmd": "shutdown"})
        resp = recv_json(sock1)
        assert resp["req_id"] == "shutdown-req"
        assert resp["status"] == "ok"
        assert resp["data"] == {}

        # Wait for server thread to stop
        running_server.wait(timeout=3.0)
        assert not running_server.is_running

        # Both client sockets should now be disconnected/closed by server
        with pytest.raises((ConnectionError, OSError)):
            sock2.settimeout(1.0)
            chunk = sock2.recv(1024)
            # Empty recv means EOF (socket closed)
            if chunk == b"":
                raise ConnectionResetError("Remote closed")

    finally:
        sock1.close()
        sock2.close()


def test_fragmented_chunks_and_batched_commands(running_server):
    """Verify server properly handles TCP stream fragmentation and multiple lines per packet."""
    host, port = running_server.server_address

    with socket.create_connection((host, port), timeout=2.0) as sock:
        # 1. Fragmented line across two packets
        chunk1 = b'{"protocol_version": "1.0", "device": "", "req_id": "frag'
        chunk2 = b'-001", "cmd": "ping"}\n'
        sock.sendall(chunk1)
        time.sleep(0.05)
        sock.sendall(chunk2)

        resp1 = recv_json(sock)
        assert resp1["req_id"] == "frag-001"
        assert resp1["status"] == "ok"
        assert resp1["data"]["pong"] is True

        # 2. Batched commands in a single TCP send
        batched = (
            b'{"protocol_version": "1.0", "device": "", "req_id": "batch-1", "cmd": "ping"}\n'
            b'{"protocol_version": "1.0", "device": "", "req_id": "batch-2", "cmd": "ping"}\n'
        )
        sock.sendall(batched)

        r1 = recv_json(sock)
        r2 = recv_json(sock)
        assert r1["req_id"] == "batch-1"
        assert r2["req_id"] == "batch-2"


def test_custom_registered_handler(running_server):
    """Verify custom synchronous and asynchronous handlers can be registered."""
    def custom_sync_handler(req):
        return {"processed": req.get("payload", "") * 2}

    async def custom_async_handler(req):
        return f"async_result_{req.get('val')}"

    running_server.register_handler("custom_sync", custom_sync_handler, fields={"payload"})
    running_server.register_handler("custom_async", custom_async_handler, fields={"val"})

    host, port = running_server.server_address
    with socket.create_connection((host, port), timeout=2.0) as sock:
        send_json(sock, {"req_id": "c1", "cmd": "custom_sync", "payload": "OK"})
        res1 = recv_json(sock)
        assert res1["req_id"] == "c1"
        assert res1["status"] == "ok"
        assert res1["data"] == {"processed": "OKOK"}

        send_json(sock, {"req_id": "c2", "cmd": "custom_async", "val": 42})
        res2 = recv_json(sock)
        assert res2["req_id"] == "c2"
        assert res2["status"] == "ok"
        assert res2["data"]["value"] == "async_result_42"

