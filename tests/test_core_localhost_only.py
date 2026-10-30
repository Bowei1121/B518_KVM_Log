import asyncio
import json
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
from tcp_server import TcpJsonServer, validate_core_host


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


@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "::1", "127.0.0.2", "localhost", "LOCALHOST", " 127.0.0.1", "127.0.0.1 ", "192.168.1.10"])
def test_other_hosts_rejected_before_socket(host, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("socket opened"))
    for validate in (validate_core_host, lambda value: TcpJsonServer(host=value)):
        with pytest.raises(ValueError) as exc:
            validate(host)
        assert repr(host) in str(exc.value)


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


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1", "localhost"])
def test_foreground_rejects_nonlocal_host(host, tmp_path, monkeypatch):
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"DFU-01": {"station": "DFU", "kvm_ip": "192.0.2.1", "channels": 4}}))
    def fail(*args, **kwargs):
        pytest.fail("invalid host reached socket or server")
    monkeypatch.setattr(core_daemon.socket, "socket", fail)
    monkeypatch.setattr(asyncio, "start_server", fail)
    monkeypatch.setattr(TcpJsonServer, "serve_forever", fail)
    monkeypatch.setattr(core_daemon.DeviceRegistry, "load", fail)
    pid, status = tmp_path / "pid", tmp_path / "status"
    with pytest.raises(SystemExit) as exc:
        core_daemon.run_service_foreground(host=host, port=0, log_file=tmp_path / "log",
            pid_file=pid, status_file=status, devices_file=devices, console=False)
    assert exc.value.code != 0
    assert not pid.exists()
    data = json.loads(status.read_text())
    assert data["state"] == "failed" and data["reason"] == "invalid_host"
    assert repr(host) in data["detail"]


def test_invalid_host_does_not_stop_or_spawn(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("invalid host reached process management")
    for name in ("graceful_stop_core_service", "force_kill_core_service", "send_tcp_command", "is_port_free", "read_pid_file"):
        monkeypatch.setattr(core_daemon, name, fail)
    monkeypatch.setattr(core_daemon.subprocess, "Popen", fail)
    for result in (core_daemon.restart_core_daemon(host="localhost"), core_daemon.start_core_daemon(host="0.0.0.0")):
        assert result["status"] == "error"


def test_ui_settings_restart_uses_localhost(monkeypatch):
    pytest.importorskip("tkinter")
    import ui_app
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(core_daemon, "restart_core_daemon", lambda **kw: calls.append(kw) or {"status": "ok"})
    ui = SimpleNamespace(client=SimpleNamespace(host="192.0.2.1", port=5000))
    assert ui_app.AtlasUI._restart_core_daemon(ui)["status"] == "ok"
    assert calls[0]["host"] == "127.0.0.1"


def test_ui_cli_restart_uses_localhost(monkeypatch):
    pytest.importorskip("tkinter")
    import ui_app
    calls = []
    monkeypatch.setattr(sys, "argv", ["ui_app.py", "--restart-core", "--host", "192.0.2.1"])
    monkeypatch.setattr(ui_app, "enable_windows_dpi_awareness", lambda: None)
    monkeypatch.setattr(core_daemon, "restart_core_daemon", lambda **kw: calls.append(kw) or {"status": "ok"})
    monkeypatch.setattr(core_daemon, "send_tcp_command", lambda *a, **kw: {"data": {"pong": True}})
    with pytest.raises(SystemExit) as exc:
        ui_app.main()
    assert exc.value.code == 0
    assert calls[0]["host"] == "127.0.0.1"
