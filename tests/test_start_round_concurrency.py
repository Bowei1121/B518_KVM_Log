"""Eight independent devices, with the production T=30 / D=103 deadline."""
import asyncio
import json
import time
import pytest
from pathlib import Path

from test_start_round import send, setup_server, run_async
from protocol_helpers import LogSolutionKVM
from kvm_pool import KVMConnectionPool


def _write_evidence(name, text):
    root = Path(__import__('tempfile').gettempdir()) / 'start-round-evidence'
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)


@pytest.mark.parametrize("slow_phase", ["connect", "monitor", "confirmation"])
def test_eight_devices_deadline_smoke(monkeypatch, tmp_path, slow_phase):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        # Registry is the only source of routing.
        kvms = {srv.device_registry.get(f'DFU-{i:02}').kvm_ip: LogSolutionKVM('preparing') for i in range(1, 9)}
        srv.kvm_pool = KVMConnectionPool(client_factory=lambda host, password='': kvms[host])
        slow = kvms[srv.device_registry.get('DFU-01').kvm_ip]
        entered = asyncio.Event()
        async def stalled():
            entered.set()
            await asyncio.Event().wait()
        if slow_phase == 'connect':
            slow.connect = stalled
        elif slow_phase == 'monitor':
            import monitor_flow
            initialize = monitor_flow.run_monitor_start
            async def monitor(kvm, *a, **kw):
                if getattr(kvm, '_kvm', kvm) is slow:
                    kw['on_accept']()
                    kw['on_preparing']()
                    entered.set()
                    return
                return await initialize(kvm, *a, **kw)
            monkeypatch.setattr(monitor_flow, 'run_monitor_start', monitor)
        else:
            launch = srv._commands['start_round'].stations['DFU']
            async def operation(kvm, **kw):
                if getattr(kvm, '_kvm', kvm) is slow:
                    kw['on_start_hid']()
                    kvm.events.append(('start',))
                    entered.set()
                    return
                return await launch(kvm, **kw)
            srv._commands['start_round'].stations['DFU'] = operation
        srv._is_running = True
        rows = []
        barrier = asyncio.Event()
        async def request(i):
            await barrier.wait()
            device = f'DFU-{i:02}'
            begin = time.monotonic()
            req = {'protocol_version': '1.0', 'cmd': 'start_round', 'req_id': 'same-key', 'device': device, 'timeout_sec': 30}
            result = json.loads(await srv._process_line(json.dumps(req).encode()))
            rows.append(dict(device=device, request=begin, response=time.monotonic(), result=result,
                             hid=kvms[srv.device_registry.get(device).kvm_ip].events.count(('start',))))
            assert not srv.lock_manager.is_locked(device)
            return result
        tasks = [asyncio.create_task(request(i)) for i in range(1, 9)]
        barrier.set()
        await entered.wait()
        fast = await asyncio.gather(*tasks[1:])
        assert all(r['data'] == {'kind': 'started'} for r in fast)
        assert not tasks[0].done()
        conflict = await send(srv, req_id='conflict')
        assert conflict['error'] == 'device_busy'
        result = await tasks[0]
        expected = {'connect': ('timeout', 'fault', 0, 103), 'monitor': ('error', 'fault', 0, 30),
                    'confirmation': ('error', 'paused', 1, 103)}[slow_phase]
        assert (result['status'], result['data']['kind']) == expected[:2]
        slow_row = next(r for r in rows if r['device'] == 'DFU-01')
        assert expected[3] <= slow_row['response'] - slow_row['request'] <= expected[3] + 7
        assert slow_row['hid'] == expected[2]
        assert all(r['hid'] == 1 and r['response'] < slow_row['response'] for r in rows if r['device'] != 'DFU-01')
        evidence_name = ('213-concurrency-evidence.json' if slow_phase == 'connect'
                        else f'213-concurrency-{slow_phase}.json')
        _write_evidence(evidence_name, json.dumps(rows, indent=2))
    run_async(scenario())


def test_eight_real_tcp_connections(monkeypatch):
    import socket
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from core_protocol import dumps_request
    from protocol_helpers import recv_reply, wire
    srv, _ = setup_server(monkeypatch)
    kvms = {srv.device_registry.get(f'DFU-{i:02}').kvm_ip: LogSolutionKVM('preparing') for i in range(1, 9)}
    slow = kvms[srv.device_registry.get('DFU-01').kvm_ip]
    async def stalled():
        await asyncio.Event().wait()
    slow.connect = stalled
    srv.kvm_pool = KVMConnectionPool(client_factory=lambda host, password='': kvms[host])
    barrier = threading.Barrier(8)
    rows = []
    def client(i):
        device = f'DFU-{i:02}'
        with socket.create_connection(srv.server_address, timeout=115) as sock:
            barrier.wait(timeout=10)
            begin = time.monotonic()
            sock.sendall(dumps_request(wire(dict(cmd='start_round', req_id='shared', device=device, timeout_sec=30))))
            result = recv_reply(sock)
            end = time.monotonic()
            row = dict(device=device, request=begin, response=end, result=result,
                       hid=kvms[srv.device_registry.get(device).kvm_ip].events.count(('start',)))
            if i != 1:
                assert result['data'] == {'kind': 'started'}
                kvms[srv.device_registry.get(device).kvm_ip].show('complete', ['PASS'] * 4)
                sock.sendall(dumps_request(wire(dict(cmd='check_finish', device=device, timeout_sec=1))))
                row['finish'] = recv_reply(sock)
                row['finish_at'] = time.monotonic()
                assert row['finish']['data']['kind'] == 'taken'
            else:
                assert result['status'] == 'timeout'
                assert 103 <= end - begin <= 110
            return row
    srv.start()
    try:
        deadline = time.monotonic() + 3
        while srv.server_address[1] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        with ThreadPoolExecutor(max_workers=8) as executor:
            rows = list(executor.map(client, range(1, 9)))
        slow_row = rows[0]
        assert slow_row['hid'] == 0
        assert all(r['hid'] == 1 and r['finish_at'] < slow_row['response'] for r in rows[1:])
        assert not srv.lock_manager.is_locked('DFU-01')
        _write_evidence('213-tcp-concurrency-evidence.json', json.dumps(rows, indent=2))
    finally:
        srv.stop()
