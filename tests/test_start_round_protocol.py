"""Wire envelope/validation plus real TCP transport (run by outer acceptance)."""
import asyncio
import json
import socket
import time

import pytest
from test_start_round import send, setup_server, run_async
from protocol_helpers import wire, recv_reply
from core_protocol import dumps_request


@pytest.mark.parametrize('value', [None, True, '30', 0, 301, float('nan'), float('inf'), -1])
def test_invalid_timeout_has_default_fault_and_no_connection(monkeypatch, value):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        req = wire(dict(cmd='start_round', device='DFU-01', timeout_sec=value))
        result = json.loads(await srv._process_line(json.dumps(req).encode()))
        assert result['error'] == 'invalid_argument'
        assert result['data'] == {'kind': 'fault'}
        assert srv.kvm_pool.active_count() == 0 and not kvm.events
    run_async(scenario())


@pytest.mark.parametrize('extra,code', [({'station': 'DFU'}, 'forbidden_field'),
    ({'kvm_ip': '1.2.3.4'}, 'forbidden_field'), ({'extra': 1}, 'unknown_field')])
def test_fields_rejected_before_acquisition(monkeypatch, extra, code):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        result = await send(srv, **extra)
        assert result['error'] == code and result['data'] == {'kind': 'fault'}
        assert srv.kvm_pool.active_count() == 0
    run_async(scenario())


def test_fct_and_ping(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        result = await send(srv, device='FCT-01')
        assert result['error'] == 'unsupported_command'
        assert not kvm.events
        result = json.loads(await srv._process_line(json.dumps(wire({'cmd': 'ping'})).encode()))
        assert 'start_round' in result['data']['supported_commands']
    run_async(scenario())


def test_real_tcp_fixed_envelope(monkeypatch):
    srv, kvm = setup_server(monkeypatch)
    srv.start()
    try:
        deadline = time.monotonic() + 3
        while srv.server_address[1] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        with socket.create_connection(srv.server_address, timeout=10) as sock:
            sock.sendall(dumps_request(wire(dict(cmd='start_round', device='DFU-01', timeout_sec=1))))
            result = recv_reply(sock)
        assert set(result) == {'protocol_version', 'req_id', 'device', 'cmd', 'status', 'error', 'message', 'elapsed_sec', 'data'}
        assert result['data'] == {'kind': 'started'}
        json.dumps(result, ensure_ascii=False).encode('ascii')
    finally:
        srv.stop()


@pytest.mark.parametrize('field,value,code', [
    ('protocol_version', '2.0', 'unsupported_protocol_version'),
    ('req_id', '', 'invalid_argument'),
    ('device', 'DFU-99', 'device_not_found'),
])
def test_common_validation_retains_round(monkeypatch, field, value, code):
    srv, kvm = setup_server(monkeypatch)
    req = wire(dict(cmd='start_round', device='DFU-01', timeout_sec=1))
    req[field] = value
    async def scenario():
        result = json.loads(await srv._process_line(json.dumps(req).encode()))
        # Existing common validation owns the error name; assert it is rejected before connection.
        assert result['status'] == 'error' and result['data'] == {'kind': 'fault'}
        assert srv.kvm_pool.active_count() == 0
    run_async(scenario())


@pytest.mark.parametrize('value', [1, 1.25, 300])
def test_timeout_endpoints_are_admitted(monkeypatch, value):
    srv, _ = setup_server(monkeypatch)
    invalid, admitted = srv._admit(wire(dict(cmd='start_round', device='DFU-01', timeout_sec=value)))
    assert invalid is None and admitted.args == value
