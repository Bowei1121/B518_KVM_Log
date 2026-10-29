"""Exercise the packaged Windows Core and bridge in CI."""
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time

# The bilingual log has Chinese; the Windows runner console is not UTF-8 by default.
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

root = Path(__file__).resolve().parents[1]
bundle = root / 'host-app/dist/AtlasKVM'
logs = Path.home() / 'Documents/logs'
template = Path.home() / 'Documents/template'
devices = template / 'devices.json'
status = logs / 'core_status.json'
log = logs / 'core_service.log'
core = bundle / 'Core.exe'
bridge = bundle / 'AtlasKVM.exe'


def run(exe, *args):
    return subprocess.run([str(exe), *args], timeout=25).returncode


def command(cmd):
    with socket.create_connection(('127.0.0.1', 5000), timeout=1) as sock:
        sock.sendall((json.dumps({'protocol_version': '1.0', 'req_id': 'ci', 'device': '', 'cmd': cmd}) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data)


def wait_for(predicate, timeout=25):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if predicate():
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.2)
    raise TimeoutError('Core did not become ready')


def state():
    return json.loads(status.read_text(encoding='utf-8'))


def check(name, action):
    try:
        action()
    except Exception:
        print(f'FAIL {name}', flush=True)
        raise
    print(f'PASS {name}', flush=True)


def shutdown():
    command('shutdown')
    wait_for(lambda: state()['state'] == 'stopped')

def check_subsystems():
    for exe in (core, bridge):
        data = exe.read_bytes()
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        assert struct.unpack_from('<H', data, offset + 0x5c)[0] == 2


def check_bridge_start():
    started = time.monotonic()
    assert run(bridge, '--restart-core') == 0
    wait_for(lambda: command('ping')['data']['pong'] is True)
    print(f'AtlasKVM.exe --restart-core cold start to pong: {time.monotonic() - started:.2f} s', flush=True)
    assert state()['state'] == 'running' and state()['pid'] and state()['port'] == 5000 and state()['started_at']
    assert 'Headless CoreDaemon started' in log.read_text(encoding='utf-8')


def check_single_instance():
    original = status.read_bytes()
    assert run(core) == 0 and status.read_bytes() == original
    result = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-NetTCPConnection -LocalPort 5000 -State Listen | Measure-Object).Count'], capture_output=True, text=True, timeout=20, check=True)
    assert int(result.stdout.strip()) == 1
    shutdown()


def check_direct_start():
    started = time.monotonic()
    proc = subprocess.Popen([str(core)])
    try:
        wait_for(lambda: command('ping')['data']['pong'] is True)
        print(f'Core.exe cold start to pong: {time.monotonic() - started:.2f} s', flush=True)
        shutdown()
        assert proc.wait(timeout=25) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=25)


def check_missing():
    devices.unlink()
    assert run(core) != 0 and state()['reason'] == 'devices_missing' and Path(state()['devices_file']) == devices
    assert 'devices_missing' in log.read_text(encoding='utf-8')


def check_invalid(content, expected):
    devices.write_text(content, encoding='utf-8')
    assert run(core) != 0 and state()['reason'] == 'devices_invalid'
    assert state()['detail'] == expected
    assert 'devices_invalid' in log.read_text(encoding='utf-8')


def check_occupied():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', 5000))
        listener.listen()
        assert run(core) != 0 and state()['reason'] == 'port_in_use'
        assert 'port_in_use' in log.read_text(encoding='utf-8')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', '(Get-Process Core -ErrorAction SilentlyContinue | Measure-Object).Count'], capture_output=True, text=True, timeout=20, check=True)
        assert int(result.stdout.strip()) == 0


try:
    sys.path.insert(0, str(root / 'host-app'))
    from device_registry import DeviceRegistry, DeviceRegistryError

    check('Windows GUI subsystems', check_subsystems)
    template.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / 'config/devices.example.json', devices)
    valid = devices.read_bytes()
    check('bridge cold start', check_bridge_start)
    check('single instance and shutdown', check_single_instance)
    check('direct Core cold start and shutdown', check_direct_start)
    check('devices_missing', check_missing)
    for name, invalid in [('invalid JSON', '{not json'), ('device validation', '{"bad": {}}')]:
        try:
            DeviceRegistry.from_text(invalid)
        except DeviceRegistryError as exc:
            expected = exc.errors[0]
        else:
            raise AssertionError('Expected invalid device registry')
        if name == 'invalid JSON':
            assert expected.startswith('invalid JSON')
        else:
            assert expected and not expected.startswith('invalid JSON')
        check(f'devices_invalid {name}', lambda: check_invalid(invalid, expected))
    devices.write_bytes(valid)
    check('port_in_use and process cleanup', check_occupied)
finally:
    if log.exists():
        print(log.read_text(encoding='utf-8', errors='replace'))
    if status.exists():
        print(status.read_text(encoding='utf-8'))
