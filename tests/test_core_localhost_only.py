import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))
sys.path.insert(0, str(ROOT / "tools"))

import core_daemon
import manage_core
from core_protocol import dumps_request
from protocol_helpers import make_registry, recv_reply, wire
from tcp_server import TcpJsonServer


def test_default_server_binds_localhost_and_pings():
    server = TcpJsonServer(port=0, device_registry=make_registry())
    server.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        assert server._server.sockets[0].getsockname()[0] == "127.0.0.1"
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.sendall(dumps_request(wire({"req_id": "ping-local", "cmd": "ping"})))
            reply = recv_reply(sock)
        assert reply["status"] == "ok"
        assert reply["data"]["pong"] is True
    finally:
        server.stop()


@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "192.168.1.10", "localhost"])
def test_other_hosts_rejected_before_socket(host, monkeypatch):
    def unexpected_socket(*args, **kwargs):
        pytest.fail("constructor opened a socket")
    monkeypatch.setattr(socket, "socket", unexpected_socket)
    with pytest.raises(ValueError, match="Core only listens on 127.0.0.1"):
        TcpJsonServer(host=host)


@pytest.mark.parametrize("action", ["run", "start", "restart"])
def test_daemon_default_host(action, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["core_daemon.py", action])
    assert core_daemon.parse_args().host == "127.0.0.1"


def test_manage_core_start_default_host(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "argv", ["manage_core.py", "start"])
    monkeypatch.setattr(manage_core, "handle_start", lambda args: seen.append(args.host) or 0)
    with pytest.raises(SystemExit) as exc:
        manage_core.main()
    assert exc.value.code == 0
    assert seen == ["127.0.0.1"]


def test_foreground_rejects_nonlocal_host(tmp_path, monkeypatch):
    monkeypatch.setattr(core_daemon.DeviceRegistry, "load", lambda *args: make_registry())
    # If the guard regresses, fail fast instead of serving forever on 0.0.0.0.
    monkeypatch.setattr(TcpJsonServer, "start", lambda self: pytest.fail("server started on a non-local host"))
    with pytest.raises(ValueError, match="Core only listens on 127.0.0.1"):
        core_daemon.run_service_foreground(
            host="0.0.0.0", port=0, log_file=tmp_path / "core.log",
            pid_file=tmp_path / "core.pid", console=False,
        )
