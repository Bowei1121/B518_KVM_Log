"""BT uses the shared explicit Core reset, without target side effects (#219)."""
import asyncio
import copy
import json
import socket
import time
from pathlib import Path

import pytest
from test_start_round import setup_server, send, run_async, _short_deadline
from test_clear_fault import clear, block, forbid_io, DEFAULT
from protocol_helpers import wire, recv_reply
from core_protocol import dumps_request
from hid_session import FlowError

BT = 'BT-01'


def snapshot(srv, device=BT):
    slot = srv._slot(device)
    state = dict(vars(slot.round))
    state['activity'] = vars(slot.round.activity)
    return copy.deepcopy((state, slot.start_round_records))


def assert_ok(result, cleared=False):
    assert (result['status'], result['error'], result['message'], result['data']) == (
        'ok', '', 'Fault cleared' if cleared else '',
        dict(cleared=cleared, was_blocked=cleared))


def test_bt_blocked_reset_allows_new_request(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch, station='BT', fail='hid_failed')
        old = await send(srv, device=BT, req_id='old')
        rec = srv._slot(BT).start_round_records['old']
        before = snapshot(srv)[0]
        assert_ok(await clear(srv, BT), True)
        assert rec.fault_cleared and rec.result['data'] == old['data']
        assert snapshot(srv)[0] == before
        assert (await send(srv, device=BT, req_id='old'))['data'] == old['data']
        assert kvm.events.count(('start',)) == 1
        async def launch(kvm, **kw):
            kw['on_start_hid']()
            kvm.events.append(('start',))
            kvm.show('monitoring', ['TESTING'] * 4)
        srv._commands['start_round'].stations['BT'] = launch
        assert (await send(srv, device=BT, req_id='fresh'))['data']['kind'] == 'started'
        assert kvm.events.count(('start',)) == 2
    run_async(scenario())


def test_bt_idle_and_repeated_reset_are_idempotent(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        before = snapshot(srv)
        assert_ok(await clear(srv, BT))
        assert snapshot(srv) == before
        block(srv, BT)
        assert_ok(await clear(srv, BT), True)
        before = snapshot(srv)
        assert_ok(await clear(srv, BT))
        assert snapshot(srv) == before
    run_async(scenario())


def test_bt_inflight_start_reset_is_rejected(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        entered, release = asyncio.Event(), asyncio.Event()
        original = srv._handle_start_round
        async def held(ctx, rec):
            entered.set()
            await release.wait()
            return await original(ctx, rec)
        srv._handle_start_round = held
        task = asyncio.create_task(send(srv, device=BT))
        await entered.wait()
        try:
            assert (await clear(srv, BT))['error'] == 'round_in_progress'
            assert not task.done() and srv.lock_manager.is_locked(BT)
            # A different device can reset while BT holds its lock.
            block(srv, 'DFU-01')
            assert_ok(await clear(srv, 'DFU-01'), True)
        finally:
            release.set()
        assert (await task)['data']['kind'] == 'started'
    run_async(scenario())


def test_bt_started_unsettled_round_reset_is_rejected(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        assert (await send(srv, device=BT))['data']['kind'] == 'started'
        before = snapshot(srv)
        assert (await clear(srv, BT))['error'] == 'round_in_progress'
        assert snapshot(srv) == before
    run_async(scenario())


def test_bt_legacy_round_and_other_lock_are_preserved(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        rec = block(srv, BT)
        rnd = srv._slot(BT).round
        rnd.begin_start()
        rnd.note_start()
        before = snapshot(srv)
        assert (await clear(srv, BT))['error'] == 'round_in_progress'
        assert snapshot(srv) == before and not rec.fault_cleared
        rnd.settle()
        assert srv.lock_manager.try_acquire(BT, holder='input')
        try:
            assert (await clear(srv, BT))['error'] == 'device_busy'
            assert srv.lock_manager.holder(BT) == 'input'
        finally:
            srv.lock_manager.release(BT)
    run_async(scenario())


async def accepted_failure(monkeypatch, mode):
    srv, kvm = setup_server(monkeypatch, station='BT', fail='hid_failed' if mode == 'paused' else None)
    if mode != 'paused':
        if mode == 'timeout':
            _short_deadline(monkeypatch)
        async def monitor(kvm, gate, key, **kw):
            kw['on_accept']()
            kw['on_preparing']()
            if mode == 'timeout':
                await asyncio.Event().wait()
            raise FlowError('monitor_not_started', 'Monitoring failed')
        monkeypatch.setattr('monitor_flow.run_monitor_start', monitor)
    result = await send(srv, device=BT, req_id='failed')
    assert result['data']['kind'] == ('paused' if mode == 'paused' else 'fault')
    assert result['status'] == ('timeout' if mode == 'timeout' else 'error')
    rec = srv._slot(BT).start_round_records['failed']
    assert rec.accepted and rec.sent == (mode == 'paused')
    events = list(kvm.events)
    assert events.count(('start',)) == (1 if mode == 'paused' else 0)
    kvm.connected = True
    kvm.show('monitoring')
    for key in ['recovered1', 'recovered2', 'recovered3']:
        await asyncio.sleep(0)
        assert (await send(srv, device=BT, req_id=key))['data']['kind'] == 'paused'
        assert not rec.fault_cleared
    before = snapshot(srv)[0]
    original = copy.deepcopy(rec.result)
    forbid_io(srv)
    assert_ok(await clear(srv, BT), True)
    assert rec.fault_cleared and rec.result == original
    assert srv._slot(BT).start_round_records['failed'] is rec
    assert snapshot(srv)[0] == before and kvm.events == events


def test_bt_accepted_fault_requires_explicit_reset(monkeypatch):
    run_async(accepted_failure(monkeypatch, 'fault'))


def test_bt_accepted_timeout_requires_explicit_reset(monkeypatch):
    run_async(accepted_failure(monkeypatch, 'timeout'))


def test_bt_hid_sent_paused_requires_explicit_reset(monkeypatch):
    run_async(accepted_failure(monkeypatch, 'paused'))


def test_bt_and_eight_dfu_resets_are_isolated(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        devices = [BT] + [f'DFU-{i:02}' for i in range(1, 9)]
        records = {d: block(srv, d) for d in devices}
        before = {d: snapshot(srv, d) for d in devices}
        assert_ok(await clear(srv, BT, req_id='shared'), True)
        for d in devices[1:]:
            assert snapshot(srv, d) == before[d]
        records[BT] = block(srv, BT)
        for d in devices[1:]:
            before = {other: snapshot(srv, other) for other in devices if other != d}
            assert_ok(await clear(srv, d, req_id='shared'), True)
            assert records[d].fault_cleared
            for other in before:
                assert snapshot(srv, other) == before[other]
        # Same-device resets and a new start are serialized by the shared lock.
        assert_ok(await clear(srv, BT), True)
        a, b, c = await asyncio.gather(clear(srv, BT), send(srv, device=BT, req_id='fresh'), clear(srv, BT))
        assert_ok(a)
        assert b['data']['kind'] == 'started' and c['error'] == 'round_in_progress'
    run_async(scenario())


def test_fct_clear_fault_remains_unsupported(monkeypatch):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        block(srv, 'FCT-01')
        before = snapshot(srv, 'FCT-01')
        result = await clear(srv, 'FCT-01')
        assert result['error'] == 'unsupported_command' and result['data'] == DEFAULT
        assert result['message'] == "'clear_fault' is only supported for DFU and BT devices"
        assert snapshot(srv, 'FCT-01') == before
    run_async(scenario())


@pytest.mark.parametrize('extra,code', [({}, 'invalid_argument'), ({'reason': None}, 'invalid_argument'),
    ({'reason': True}, 'invalid_argument'), ({'reason': 1}, 'invalid_argument'),
    ({'reason': 'reset'}, 'invalid_argument'), ({'reason': 'operator_reset', 'typo': 1}, 'unknown_field'),
    ({'reason': 'operator_reset', 'station': 'BT'}, 'forbidden_field'),
    ({'reason': 'operator_reset', 'kvm_ip': 'x'}, 'forbidden_field')])
def test_bt_clear_fault_validation_preserves_state(monkeypatch, extra, code):
    async def scenario():
        srv, _ = setup_server(monkeypatch, station='BT')
        block(srv, BT)
        before = snapshot(srv)
        forbid_io(srv)
        result = json.loads(await srv._process_line(json.dumps(wire(dict(cmd='clear_fault', device=BT, **extra))).encode()))
        assert result['error'] == code and result['data'] == DEFAULT
        assert snapshot(srv) == before
    run_async(scenario())


def test_bt_clear_fault_performs_no_target_io(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch, station='BT')
        block(srv, BT)
        kvm.connected = False
        forbid_io(srv)
        def forbidden(*a, **kw):
            pytest.fail('clear_fault attempted target I/O')
        for name in ['latest_frame', 'click', 'move', 'press_command_shift_m']:
            monkeypatch.setattr(kvm, name, forbidden)
        assert_ok(await clear(srv, BT), True)
        assert_ok(await clear(srv, BT))
        req = wire(dict(cmd='clear_fault', device=BT, reason='bad'))
        assert json.loads(await srv._process_line(json.dumps(req).encode()))['error'] == 'invalid_argument'
        srv._slot(BT).round.begin_start()
        srv._slot(BT).round.note_start()
        assert (await clear(srv, BT))['error'] == 'round_in_progress'
        assert not kvm.events
    run_async(scenario())


def test_bt_clear_fault_tcp_envelope_and_ascii(monkeypatch):
    srv, _ = setup_server(monkeypatch, station='BT')
    block(srv, BT)
    forbid_io(srv)
    srv.port = 0
    srv.start()
    try:
        deadline = time.monotonic() + 3
        while srv.server_address[1] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        with socket.create_connection(srv.server_address, timeout=3) as sock:
            for device, reason, code, message, data in [
                (BT, 'operator_reset', '', 'Fault cleared', dict(cleared=True, was_blocked=True)),
                (BT, 'operator_reset', '', '', DEFAULT),
                (BT, 'operator_reset', 'round_in_progress', 'A round is still in progress', DEFAULT),
                ('FCT-01', 'operator_reset', 'unsupported_command', "'clear_fault' is only supported for DFU and BT devices", DEFAULT),
                (BT, 'bad', 'invalid_argument', 'reason must be operator_reset', DEFAULT),
            ]:
                if code == 'round_in_progress':
                    srv._slot(BT).round.begin_start()
                    srv._slot(BT).round.note_start()
                req = wire(dict(cmd='clear_fault', device=device, reason=reason))
                sock.sendall(dumps_request(req))
                result = recv_reply(sock)
                assert set(result) == {'protocol_version', 'req_id', 'device', 'cmd', 'status', 'error', 'message', 'elapsed_sec', 'data'}
                assert result['protocol_version'] == '1.0' and result['req_id'] == req['req_id']
                assert result['device'] == device and result['cmd'] == 'clear_fault'
                assert result['status'] == ('error' if code else 'ok')
                assert result['error'] == code and result['data'] == data
                if message is not None:
                    assert result['message'] == message
                json.dumps(result, ensure_ascii=False).encode('ascii')
    finally:
        srv.stop()


def test_windows_ci_includes_bt_clear_fault_tests():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/build-windows.yml').read_text()
    command = next(line for line in workflow.splitlines() if 'run: python -m pytest ' in line)
    assert 'tests/test_clear_fault_bt_219.py' in command.split()
    assert '--capture=sys' in command and 'faulthandler_timeout=120' in command
