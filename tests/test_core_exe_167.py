import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host-app'))
import core_daemon as core
import device_registry


def test_launch_command_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert core.core_launch_command(['--port', '123'])[1].endswith('core_daemon.py')
    monkeypatch.setattr(sys, 'frozen', True)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'AtlasKVM.exe'))
    exe = tmp_path / ('Core.exe' if sys.platform == 'win32' else 'Core')
    exe.touch()
    assert core.core_launch_command([]) == [str(exe), 'run']
    assert core.default_log_file().parent == core.default_status_file().parent
    assert core.default_pid_file().parent == core.default_status_file().parent
    args = core.parse_args([])
    assert args.command == 'run' and args.host == '127.0.0.1'


@pytest.mark.parametrize('content,reason', [(None, 'devices_missing'), ('{bad', 'devices_invalid'), ('{"bad": {}}', 'devices_invalid')])
def test_invalid_devices(tmp_path, content, reason):
    devices = tmp_path / 'devices.json'
    if content is not None:
        devices.write_text(content, encoding='utf-8')
    status = tmp_path / 'status.json'
    cmd = [sys.executable, str(Path(core.__file__)), 'run', '--port', '0', '--devices-file', str(devices), '--status-file', str(status), '--log-file', str(tmp_path / 'log'), '--pid-file', str(tmp_path / 'pid')]
    result = subprocess.run(cmd, timeout=30)
    assert result.returncode != 0
    data = json.loads(status.read_text(encoding='utf-8'))
    assert data['reason'] == reason and data['devices_file'] == str(devices.resolve())
    assert data['detail']
    assert reason in (tmp_path / 'log').read_text(encoding='utf-8')
    if content is None:
        assert str(devices.resolve()) in data['detail'] or str(devices.resolve()) == data['devices_file']
    else:
        with pytest.raises(device_registry.DeviceRegistryError) as exc:
            device_registry.DeviceRegistry.from_text(content)
        assert data['detail'] == exc.value.errors[0]
    assert not (tmp_path / 'pid').exists()


def test_foreground_lifecycle_and_single_instance(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    status, pid, log = (tmp_path / name for name in ('status', 'pid', 'log'))
    cmd = [sys.executable, core.__file__, 'run', '--port', str(port), '--devices-file', str(devices), '--status-file', str(status), '--pid-file', str(pid), '--log-file', str(log)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if core.send_tcp_command('ping', port=port, timeout=.2)['data']['pong']:
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Core did not start')
        before = status.read_bytes()
        data = json.loads(before)
        assert data['state'] == 'running' and data['pid'] == proc.pid and data['started_at'] and data['port'] == port
        assert subprocess.run(cmd, timeout=20).returncode == 0
        assert status.read_bytes() == before and int(pid.read_text(encoding='utf-8')) == proc.pid
        core.send_tcp_command('shutdown', port=port)
        assert proc.wait(timeout=20) == 0
        assert json.loads(status.read_text(encoding='utf-8'))['state'] == 'stopped'
        assert not pid.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_port_in_use(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        port = sock.getsockname()[1]
        status = tmp_path / 'status'
        log = tmp_path / 'log'
        cmd = [sys.executable, core.__file__, 'run', '--port', str(port), '--devices-file', str(devices), '--status-file', str(status), '--log-file', str(log), '--pid-file', str(tmp_path / 'pid')]
        assert subprocess.run(cmd, timeout=20).returncode != 0
        assert json.loads(status.read_text(encoding='utf-8'))['reason'] == 'port_in_use'
        assert 'port_in_use' in log.read_text(encoding='utf-8')


def test_source_start_restart(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    args = ['--port', str(port), '--devices-file', str(devices), '--status-file', str(tmp_path / 'status'), '--pid-file', str(tmp_path / 'pid'), '--log-file', str(tmp_path / 'log')]
    try:
        for command in ('start', 'restart'):
            result = subprocess.run([sys.executable, core.__file__, command, *args], capture_output=True, timeout=30)
            log = tmp_path / 'log'
            log_text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else '<missing>'
            assert result.returncode == 0, (f"{command}: stdout={result.stdout!r}, "
                                            f"stderr={result.stderr!r}, log={log_text}")
            assert core.send_tcp_command('ping', port=port)['data']['pong']
    finally:
        try:
            core.send_tcp_command('shutdown', port=port)
        except OSError:
            pass


def test_start_missing_devices_preserves_child_failure(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    status = tmp_path / 'status'
    args = ['--port', str(port), '--devices-file', str(tmp_path / 'missing'),
            '--status-file', str(status), '--pid-file', str(tmp_path / 'pid'),
            '--log-file', str(tmp_path / 'log'), '--timeout', '5']
    try:
        result = subprocess.run([sys.executable, core.__file__, 'start', *args], capture_output=True, timeout=20)
        assert result.returncode != 0
        assert json.loads(status.read_text(encoding='utf-8'))['reason'] == 'devices_missing'
        assert 'devices_missing' in (tmp_path / 'log').read_text(encoding='utf-8')
    finally:
        try:
            core.send_tcp_command('shutdown', port=port, timeout=.2)
        except OSError:
            pass


def test_failed_start_preserves_running_core_status(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    status = tmp_path / 'status'
    common = ['--port', str(port), '--devices-file', str(devices), '--status-file', str(status), '--log-file', str(tmp_path / 'log')]
    proc = subprocess.Popen([sys.executable, core.__file__, 'run', *common, '--pid-file', str(tmp_path / 'pid1')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if core.send_tcp_command('ping', port=port, timeout=.2)['data']['pong']:
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Core did not start')
        before = json.loads(status.read_text(encoding='utf-8'))
        assert before['state'] == 'running' and before['pid'] == proc.pid
        result = subprocess.run([sys.executable, core.__file__, 'start', *common, '--pid-file', str(tmp_path / 'pid2'), '--timeout', '5'], capture_output=True, timeout=20)
        assert result.returncode != 0
        after = json.loads(status.read_text(encoding='utf-8'))
        assert after['state'] == 'running' and after['pid'] == proc.pid
        assert core.send_tcp_command('ping', port=port, timeout=1)['data']['pong']
    finally:
        if proc.poll() is None:
            try:
                core.send_tcp_command('shutdown', port=port, timeout=1)
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=10)


def test_unhandled_exception_records_failure(monkeypatch, tmp_path):
    status = tmp_path / 'status'
    log = tmp_path / 'log'
    args = core.parse_args(['run', '--status-file', str(status), '--log-file', str(log), '--devices-file', str(tmp_path / 'devices')])
    monkeypatch.setattr(core, 'parse_args', lambda: args)
    def broken(args):
        raise RuntimeError('injected failure')
    monkeypatch.setattr(core, 'main', broken)
    with pytest.raises(SystemExit) as exc:
        core.entrypoint()
    assert exc.value.code != 0
    assert json.loads(status.read_text(encoding='utf-8'))['reason'] == 'internal_error'
    assert 'injected failure' in log.read_text(encoding='utf-8')


def test_bridge_restart_core_without_tk(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), B518_DEVICES_FILE=str(devices))
    try:
        result = subprocess.run([sys.executable, str(Path(core.__file__).with_name('ui_app.py')), '--restart-core', '--port', str(port), '--pid-file', str(tmp_path / 'pid'), '--log-file', str(tmp_path / 'log'), '--status-file', str(tmp_path / 'status')], env=env, capture_output=True, timeout=40)
        assert result.returncode == 0, result.stderr
        assert core.send_tcp_command('ping', port=port)['data']['pong']
    finally:
        pid = core.read_pid_file(tmp_path / 'pid')
        try:
            core.send_tcp_command('shutdown', port=port)
        except OSError:
            pass
        if pid:
            deadline = time.monotonic() + 20
            while core.is_pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(.1)
            assert not core.is_pid_alive(pid)


def test_frozen_default_paths(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    exe_dir = tmp_path / 'bundle'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe_dir / 'AtlasKVM.exe'))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    assert core.default_log_file() == home / 'Documents' / 'logs' / 'core_service.log'
    assert core.default_pid_file() == home / 'Documents' / 'logs' / 'core_service.pid'
    assert core.default_status_file() == home / 'Documents' / 'logs' / 'core_status.json'


def test_wildcard_host_single_instance(tmp_path):
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU', 'kvm_ip': '192.0.2.1', 'channels': 4}}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [sys.executable, core.__file__, 'run', '--host', '0.0.0.0', '--port', str(port), '--devices-file', str(devices), '--status-file', str(tmp_path / 'status'), '--log-file', str(tmp_path / 'log'), '--pid-file', str(tmp_path / 'pid')]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if core.send_tcp_command('ping', host='127.0.0.1', port=port, timeout=.2)['data']['pong']:
                    break
            except OSError:
                time.sleep(.1)
        else:
            pytest.fail('Core did not start')
        assert subprocess.run(cmd, timeout=20).returncode == 0
        core.send_tcp_command('shutdown', port=port)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=20)


def test_is_pid_alive_for_current_and_finished_process():
    # Runs on Windows CI too (test_core_daemon.py is excluded there, #111):
    # os.kill(pid, 0) is not a liveness probe on Windows (signal 0 == CTRL_C_EVENT).
    assert core.is_pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait(timeout=20)
    assert not core.is_pid_alive(child.pid)
