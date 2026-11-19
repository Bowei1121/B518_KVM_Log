"""Explicit per-device Core reset; no target I/O."""
import asyncio
import copy
import json
import socket
import time

import pytest
from test_start_round import setup_server, send, run_async
from start_round import Record, reply
from protocol_helpers import wire, recv_reply
from core_protocol import dumps_request

DEFAULT = {'cleared': False, 'was_blocked': False}

async def clear(srv, device='DFU-01', **extra):
    req = wire(dict(cmd='clear_fault', device=device, reason='operator_reset', **extra))
    return json.loads(await srv._process_line(json.dumps(req).encode()))

def block(srv, device='DFU-01', kind='fault', status='error'):
    rec = Record(1, 0, accepted=True, round_generation=srv._slot(device).round.start_generation, result=reply('timeout' if status == 'timeout' else 'hid_failed', 'Failed', kind, status))
    srv._slot(device).start_round_records['old'] = rec
    srv._slot(device).round.note_start()
    return rec

def forbid_io(srv):
    async def forbidden(*a, **kw):
        pytest.fail('clear_fault attempted target I/O')
    srv.kvm_pool.acquire_connection = forbidden
    srv._neutralize_manual_hid_async = forbidden
    srv._release_dropped_manual = forbidden

@pytest.mark.parametrize('kind,status', [('fault','error'), ('fault','timeout'), ('paused','error')])
def test_reset_preserves_records_round_and_no_io(monkeypatch, kind, status):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        rec = block(srv, kind=kind, status=status)
        for key in ['new1', 'new2']:
            assert (await send(srv, req_id=key))['message'] == 'Start round blocked for device; operator reset required'
        before = copy.deepcopy(vars(srv._slot('DFU-01').round))
        original = copy.deepcopy(rec.result)
        acquire = srv.kvm_pool.acquire_connection
        neutralize, reconcile = srv._neutralize_manual_hid_async, srv._release_dropped_manual
        forbid_io(srv)
        result = await clear(srv)
        assert result['message'] == 'Fault cleared' and result['data'] == dict(cleared=True, was_blocked=True)
        assert rec.result == original and len(srv._slot('DFU-01').start_round_records) == 1
        rnd = srv._slot('DFU-01').round
        for field in ['start_requested', 'taken', 'input_released', 'delivered_results', 'delivered_signature']:
            assert getattr(rnd, field) == before[field]
        assert (await clear(srv))['data'] == DEFAULT
        assert (await clear(srv))['message'] == ''
        assert not kvm.events
        assert (await send(srv, req_id='old'))['data']['kind'] == 'paused'
        srv.kvm_pool.acquire_connection = acquire
        srv._neutralize_manual_hid_async, srv._release_dropped_manual = neutralize, reconcile
        assert (await send(srv, req_id='fresh'))['data']['kind'] == 'started'
        assert kvm.events.count(('start',)) == 1
    run_async(scenario())

@pytest.mark.parametrize('extra,code', [({'reason':None},'invalid_argument'), ({'reason':True},'invalid_argument'),
    ({'reason':'reset'},'invalid_argument'), ({'typo':1},'unknown_field'), ({'station':'DFU'},'forbidden_field'),
    ({'kvm_ip':'x'},'forbidden_field'), ({'device':'DFU-99'},'unknown_device'),
    ({'device':'FCT-01'},'unsupported_command'), ({'device':'BT-01'},'unsupported_command'),
    ({'protocol_version':'2.0'},'unsupported_protocol_version'), ({'req_id':''},'missing_req_id')])
def test_rejections_no_io_or_mutation(monkeypatch, extra, code):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        rec = block(srv)
        forbid_io(srv)
        req = wire(dict(cmd='clear_fault', device='DFU-01', reason='operator_reset'))
        req.update(extra)
        result = json.loads(await srv._process_line(json.dumps(req).encode()))
        assert result['error'] == code and result['data'] == DEFAULT
        assert not rec.fault_cleared and not kvm.events
    run_async(scenario())

def test_missing_reason_and_idle(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        forbid_io(srv)
        req = wire(dict(cmd='clear_fault', device='DFU-01'))
        result = json.loads(await srv._process_line(json.dumps(req).encode()))
        assert result['error'] == 'invalid_argument' and result['data'] == DEFAULT
        assert (await clear(srv))['data'] == DEFAULT
    run_async(scenario())

def test_inflight_start_race_and_successful_unsettled_round(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        original = srv._handle_start_round
        async def held(ctx, rec):
            entered.set()
            await release.wait()
            return await original(ctx, rec)
        srv._handle_start_round = held
        task = asyncio.create_task(send(srv))
        await entered.wait()
        assert (await clear(srv))['error'] == 'round_in_progress'
        assert srv.lock_manager.is_locked('DFU-01')
        release.set()
        assert (await task)['data']['kind'] == 'started'
        assert (await clear(srv))['error'] == 'round_in_progress'
        srv._slot('DFU-01').round.settle()
        assert (await clear(srv))['data'] == DEFAULT
        assert srv.lock_manager.try_acquire('DFU-01', holder='input')
        assert (await clear(srv))['error'] == 'device_busy'
        assert srv.lock_manager.holder('DFU-01') == 'input'
        srv.lock_manager.release('DFU-01')
    run_async(scenario())

def test_nine_devices_same_key_only_selected_reset(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        first, second = block(srv), block(srv, 'DFU-02')
        gate = asyncio.Event()
        async def request(device):
            await gate.wait()
            if device == 'DFU-01':
                return await clear(srv, req_id='shared')
            return await send(srv, device=device, req_id='shared')
        tasks = [asyncio.create_task(request(f'DFU-{i:02}')) for i in range(1,10)]
        gate.set()
        results = await asyncio.gather(*tasks)
        assert results[0]['data']['cleared'] and first.fault_cleared
        assert results[1]['data']['kind'] == 'paused' and not second.fault_cleared
        assert all(r['data']['kind'] == 'started' for r in results[2:])
        assert (await clear(srv))['data'] == DEFAULT
        # Concurrent duplicate resets and a new start remain serialized on this device.
        a, b, c = await asyncio.gather(clear(srv), send(srv, req_id='fresh'), clear(srv))
        assert a['data'] == DEFAULT and b['data']['kind'] == 'started'
        assert c['error'] == 'round_in_progress'
    run_async(scenario())

def test_actual_tcp_success_message_and_ping(monkeypatch):
    srv, _ = setup_server(monkeypatch)
    block(srv)
    forbid_io(srv)
    srv.port = 0
    srv.start()
    try:
        deadline = time.monotonic()+3
        while srv.server_address[1] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        with socket.create_connection(srv.server_address, timeout=3) as sock:
            for cmd in ['clear_fault', 'clear_fault', 'ping']:
                sock.sendall(dumps_request(wire(dict(cmd=cmd, device='' if cmd == 'ping' else 'DFU-01', **({'reason':'operator_reset'} if cmd == 'clear_fault' else {})))))
                result = recv_reply(sock)
                assert set(result) == {'protocol_version','req_id','device','cmd','status','error','message','elapsed_sec','data'}
                json.dumps(result, ensure_ascii=False).encode('ascii')
                if cmd == 'ping':
                    assert result['message'] == '' and result['data']['pong']
                    commands = result['data']['supported_commands']
                    assert commands == sorted(set(commands)) and 'clear_fault' in commands
                elif result['data']['cleared']:
                    assert result['message'] == 'Fault cleared'
                else:
                    assert result['message'] == '' and result['data'] == DEFAULT
    finally:
        srv.stop()

def test_legacy_round_after_failed_start_is_not_cleared(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        rec = block(srv)
        rnd = srv._slot('DFU-01').round
        rnd.begin_start()
        rnd.note_start()
        forbid_io(srv)
        result = await clear(srv)
        assert result['error'] == 'round_in_progress' and result['data'] == DEFAULT
        assert not rec.fault_cleared
    run_async(scenario())

def test_serialized_contract_evidence(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        block(srv)
        forbid_io(srv)
        replies = [await clear(srv), await clear(srv)]
        rnd = srv._slot('DFU-01').round
        rnd.begin_start()
        rnd.note_start()
        replies += [await clear(srv), await clear(srv, device='FCT-01'), await clear(srv, device='BT-01')]
        assert [r['error'] for r in replies] == ['', '', 'round_in_progress', 'unsupported_command', 'unsupported_command']
        from pathlib import Path
        Path('/tmp/217-envelopes.json').write_text(json.dumps(replies, ensure_ascii=False, indent=2))
        # Previous command success messages still serialize as empty.
        old = json.loads(srv._encode_result(time.monotonic(), 'old', '', 'ping', dict(status='ok', message='Ignored')))
        assert old['message'] == ''
    run_async(scenario())

@pytest.mark.parametrize('sent', [False, True])
def test_real_accepted_failure_remains_blocked_after_recovery(monkeypatch, sent):
    async def scenario():
        srv, kvm = setup_server(monkeypatch, fail='hid_failed' if sent else None)
        if not sent:
            from hid_session import FlowError
            async def fail_monitor(kvm, gate, key, **kwargs):
                kwargs['on_accept']()
                kwargs['on_preparing']()
                raise FlowError('monitor_not_started', 'Monitoring failed')
            monkeypatch.setattr('monitor_flow.run_monitor_start', fail_monitor)
        result = await send(srv, req_id='failed')
        assert result['data']['kind'] == ('paused' if sent else 'fault')
        events = list(kvm.events)
        kvm.show('monitoring')  # Restored display/connection do not clear Core failure.
        for key in ['after-recovery-1', 'after-recovery-2']:
            await asyncio.sleep(0)
            assert (await send(srv, req_id=key))['data']['kind'] == 'paused'
        assert kvm.events == events
        forbid_io(srv)
        assert (await clear(srv))['data']['cleared']
        assert kvm.events == events
    run_async(scenario())

def test_real_accepted_timeout_can_reset(monkeypatch):
    from test_start_round import _short_deadline
    async def scenario():
        srv, _ = setup_server(monkeypatch)
        _short_deadline(monkeypatch)
        async def monitor(kvm, gate, key, **kw):
            kw['on_accept']()
            kw['on_preparing']()
            await asyncio.Event().wait()
        monkeypatch.setattr('monitor_flow.run_monitor_start', monitor)
        result = await send(srv)
        assert result['status'] == 'timeout' and result['data']['kind'] == 'fault'
        assert (await send(srv, req_id='new1'))['data']['kind'] == 'paused'
        assert (await send(srv, req_id='new2'))['data']['kind'] == 'paused'
        forbid_io(srv)
        assert (await clear(srv))['data']['cleared']
    run_async(scenario())
