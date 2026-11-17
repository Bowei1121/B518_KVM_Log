"""Start-round orchestration with contract 1.3 frames, without opening a listener."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host-app'))
from tcp_server import TcpJsonServer
from protocol_helpers import LogSolutionKVM, make_log_solution_pool, make_registry, wire
from hid_session import FlowError



def _write_evidence(name, text):
    root = Path(__import__('tempfile').gettempdir()) / 'start-round-evidence'
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)

def setup_server(monkeypatch, station='DFU', fail=None):
    kvm = LogSolutionKVM('preparing')
    srv = TcpJsonServer(device_registry=make_registry(), kvm_pool=make_log_solution_pool(kvm))
    async def picture(ctx):
        return None
    srv._picture_refusal = picture
    async def monitor(kvm, gate, key, **kwargs):
        kwargs['on_accept']()
        kwargs['on_preparing']()
        kvm.show('monitoring')
    monkeypatch.setattr('monitor_flow.run_monitor_start', monitor)
    async def operation(kvm, **kwargs):
        kwargs['on_start_hid']()
        kvm.events.append(('start',))
        if fail:
            raise FlowError(fail, 'lost reply')
        kvm.show('monitoring', ['TESTING', 'WAITING', 'WAITING', 'WAITING'])
    spec = srv._commands['start_round']
    spec.stations[station] = operation
    return srv, kvm


async def send(srv, req_id='round1', device='DFU-01', **kwargs):
    req = wire(dict(cmd='start_round', req_id=req_id, device=device, timeout_sec=1, **kwargs))
    return json.loads(await srv._process_line(json.dumps(req).encode()))


@pytest.mark.parametrize('station', ['DFU', 'BT'])
def test_started_requires_activity_and_does_not_deliver(monkeypatch, station):
    async def scenario():
        srv, kvm = setup_server(monkeypatch, station)
        device = station + '-01'
        result = await send(srv, device=device)
        assert result['data'] == {'kind': 'started'}, result
        rnd = srv._slot(device).round
        assert not rnd.taken and not rnd.input_released and rnd.start_requested
        again = await send(srv, device=device)
        assert again['data'] == {'kind': 'started'}
        assert kvm.events.count(('start',)) == 1
        assert not srv.lock_manager.is_locked(device)
        assert srv.kvm_pool.get_connection_info(make_registry().get(device).kvm_ip)['in_use'] == 0
    run_async(scenario())


def test_lost_hid_reply_is_paused_and_never_repeated(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch, fail='hid_failed')
        for key in ['round1', 'round1', 'round2']:
            result = await send(srv, req_id=key)
            assert result['data'] == {'kind': 'paused'}
        assert kvm.events.count(('start',)) == 1
    run_async(scenario())


def test_review_before_accept_preserves_previous_round(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        rnd = srv._slot('DFU-01').round
        rnd.armed = True
        rnd.input_released = True
        async def review(*a, **kw):
            raise FlowError('log_solution_review', 'Review required')
        monkeypatch.setattr('monitor_flow.run_monitor_start', review)
        result = await send(srv)
        assert result['data'] == {'kind': 'paused'}
        assert rnd.armed and rnd.input_released
        assert not kvm.events
    run_async(scenario())


def test_cancel_connect_releases_lock_and_no_late_hid(monkeypatch):
    async def scenario():
        srv, kvm = setup_server(monkeypatch)
        entered = asyncio.Event()
        async def connect(ip):
            entered.set()
            await asyncio.Event().wait()
        srv.kvm_pool.acquire_connection = connect
        task = asyncio.create_task(send(srv))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not srv.lock_manager.is_locked('DFU-01')
        assert not kvm.events
        result = await send(srv)
        assert result['data'] == {'kind': 'paused'}
    run_async(scenario())


def run_async(coro):
    # A periodic loop tick also lets worker completions progress in restricted
    # environments that deny the selector's self-pipe send(). It changes no flow budget.
    loop = asyncio.new_event_loop()
    async def tick():
        while True:
            await asyncio.sleep(.01)
    ticker = loop.create_task(tick())
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        ticker.cancel()
        loop.run_until_complete(asyncio.gather(ticker, return_exceptions=True))
        loop.close()


def test_poll_budget_three_shares_including_retry_intervals(monkeypatch):
    import start_round as flow
    from types import SimpleNamespace
    from tcp_server import CommandContext
    now = [0.]
    async def sleep(seconds):
        now[0] += seconds
    async def read(kvm, gate):
        return SimpleNamespace(kind='waiting', state=None)
    srv, kvm = setup_server(monkeypatch)
    kvm.connected = True
    async def monitor(*a, **kw):
        kw['on_accept']()
        kw['on_preparing']()
    monkeypatch.setattr('monitor_flow.run_monitor_start', monitor)
    monkeypatch.setattr(flow, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(flow, 'asyncio', SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(flow, 'read', read)
    slot = srv._slot('DFU-01')
    ctx = CommandContext(srv.device_registry.get('DFU-01'), kvm, slot)
    record = flow.Record(30, 103)
    result = run_async(flow.run(srv, ctx, record))
    assert result['error'] == 'monitor_not_started'
    assert now[0] == pytest.approx(30)
    polls = [row for row in record.measurements if row[0] == "monitor_poll"]
    assert len(polls) == 3
    assert [row[1] for row in polls] == pytest.approx([10, 10, 9])
    assert record.deadline == 103
    assert not record.sent and not kvm.events


def test_structured_no_frame_retries_exactly_three(monkeypatch):
    import start_round as flow
    from types import SimpleNamespace
    from tcp_server import CommandContext

    srv, kvm = setup_server(monkeypatch)
    now = [0.]
    calls = []
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    async def no_frame(*a, **kw):
        calls.append(now[0])
        raise FlowError('hid_failed', 'No image', reason='no_frame')
    monkeypatch.setattr('monitor_flow.run_monitor_start', no_frame)
    monkeypatch.setattr(flow, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(flow, 'asyncio', SimpleNamespace(sleep=sleep))
    ctx = CommandContext(srv.device_registry.get('DFU-01'), kvm, srv._slot('DFU-01'))
    # T=2 admits two 0.5s intervals without resetting the retry window.
    record = flow.Record(2, 75)
    result = run_async(flow.run(srv, ctx, record))
    assert result['error'] == 'hid_failed' and result['data'] == {'kind': 'fault'}
    assert calls == [0., .5, 1.]
    assert sleeps == [.5, .5]
    attempts = [row for row in record.measurements if row[0] == 'monitor_initialize']
    assert [row[2] for row in attempts] == [75., 74.5, 74.]
    assert record.deadline == 75
    assert not kvm.events


def test_unstructured_hid_failure_not_retried(monkeypatch):
    srv, kvm = setup_server(monkeypatch)
    calls = []
    async def failure(*a, **kw):
        calls.append(1)
        raise FlowError('hid_failed', 'No JetKVM frame available')
    monkeypatch.setattr('monitor_flow.run_monitor_start', failure)
    result = run_async(send(srv))
    assert result['data'] == {'kind': 'fault'} and calls == [1]


def test_login_does_not_block_loop_and_cancel_never_enters_ice(monkeypatch):
    import threading
    import jetkvm_core
    entered = threading.Event()
    release = threading.Event()
    def login(self):
        entered.set()
        release.wait(2)
        return 'late-cookie'
    monkeypatch.setattr(jetkvm_core.JetKVMClient, '_login_cookie', login)
    async def scenario():
        client = jetkvm_core.JetKVMClient('192.0.2.1')
        task = asyncio.create_task(client.connect())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            assert entered.is_set()
            assert client.pc is None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client.pc is None
        finally:
            release.set()
    run_async(scenario())


def test_real_monitor_preparing_to_monitoring_seam(tmp_path):
    kvm = LogSolutionKVM('preparing')
    srv = TcpJsonServer(device_registry=make_registry(), kvm_pool=make_log_solution_pool(kvm), template_root=tmp_path)
    async def launch(kvm, **kwargs):
        kwargs['on_start_hid']()
        kvm.events.append(('start',))
        kvm.show('monitoring', ['TESTING'] * 4)
    srv._commands['start_round'].stations['DFU'] = launch
    async def scenario():
        task = asyncio.create_task(send(srv))
        while not srv._slot('DFU-01').round.start_requested:
            if task.done():
                pytest.fail(str(await task))
            await asyncio.sleep(.01)
        kvm.show('monitoring')
        result = await task
        assert result['data'] == {'kind': 'started'}, result
        record = srv._slot('DFU-01').start_round_records['round1']
        _write_evidence('213-budget-evidence.json', json.dumps({
            'source': 'contract 1.3 fake KVM; real monitor initialization and frame checks; launch operation stub',
            'timeout_sec': record.timeout, 'deadline': record.deadline,
            'measurements': record.measurements}, indent=2))
        assert kvm.events.count(('start',)) == 1
        assert not any(e[0] == 'key' for e in kvm.events)
    run_async(scenario())


@pytest.mark.parametrize('statuses', [['WAITING'] * 4, ['NOTEST'] * 4])
def test_monitoring_or_notest_alone_never_means_started(monkeypatch, statuses):
    import start_round
    import time
    from tcp_server import CommandContext
    srv, kvm = setup_server(monkeypatch)
    kvm.connected = True
    async def launch(kvm, **kw):
        kw['on_start_hid']()
        kvm.show('monitoring', statuses)
    ctx = CommandContext(srv.device_registry.get('DFU-01'), kvm, srv._slot('DFU-01'), launch)
    rec = start_round.Record(1, time.monotonic() + .25)
    result = run_async(start_round.run(srv, ctx, rec))
    assert rec.sent and result['data'] == {'kind': 'paused'}
    assert result['error'] == 'hid_failed'
    assert not ctx.slot.round.taken and not ctx.slot.round.input_released


@pytest.mark.parametrize('termination', ['cancel', 'deadline'])
@pytest.mark.parametrize('phase', ['ice', 'websocket', 'rpc', 'first_frame'])
def test_real_connect_path_cancellation_cleans_each_wait(monkeypatch, phase, termination):
    import jetkvm_core as jet
    from types import SimpleNamespace
    from kvm_pool import KVMConnectionPool
    seen = []
    class Rpc:
        def on(self, event, callback=None):
            if callback is not None:
                return callback
            def register(fn):
                if event == 'open' and phase == 'first_frame':
                    fn()
                return fn
            return register
    class Peer:
        iceGatheringState = 'gathering' if phase == 'ice' else 'complete'
        localDescription = SimpleNamespace(type='offer', sdp='v=0')
        closed = False
        def __init__(self, *a):
            seen.append(self)
        def createDataChannel(self, name):
            return Rpc()
        def addTransceiver(self, *a, **kw):
            pass
        def on(self, event):
            return lambda fn: fn
        async def createOffer(self):
            return self.localDescription
        async def setLocalDescription(self, value):
            pass
        async def close(self):
            self.closed = True
    class Ws:
        async def send(self, value):
            pass
        def __aiter__(self):
            return self
        async def __anext__(self):
            await asyncio.Event().wait()
        async def close(self):
            pass
    async def websocket(*a, **kw):
        if phase == 'websocket':
            await asyncio.Event().wait()
        return Ws()
    def watch(self, pc, first_frame):
        if phase != 'first_frame':
            first_frame.set()
    monkeypatch.setattr(jet, 'RTCPeerConnection', Peer)
    monkeypatch.setattr(jet.websockets, 'connect', websocket)
    monkeypatch.setattr(jet.JetKVMClient, '_watch_session', watch)
    srv, _ = setup_server(monkeypatch)
    srv.kvm_pool = KVMConnectionPool(client_factory=jet.JetKVMClient)
    async def scenario():
        if termination == 'deadline':
            import start_round
            observed_timeouts = []
            async def controlled_wait(awaitable, timeout):
                observed_timeouts.append(timeout)
                return await asyncio.wait_for(awaitable, min(timeout, .1))
            monkeypatch.setattr(start_round, 'asyncio', SimpleNamespace(wait_for=controlled_wait, TimeoutError=asyncio.TimeoutError, CancelledError=asyncio.CancelledError))
        task = asyncio.create_task(send(srv))
        if termination == 'cancel':
            await asyncio.sleep(.1)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result['status'] == 'timeout' and result['data'] == {'kind': 'fault'}
            assert 73 < observed_timeouts[0] <= 74  # T=1, unchanged D=T+73.
        assert seen and seen[0].closed
        assert not srv.lock_manager.is_locked('DFU-01')
        assert srv.kvm_pool.active_count() == 0
    run_async(scenario())


def test_hid_boundary_follows_handover_and_precedes_send(tmp_path):
    from hid_session import HidSession
    events = []
    class Kvm:
        rpc_errors = []
        async def prepare_automated_hid(self):
            events.append('handover')
        async def click(self, *args):
            events.append('sent')
            raise FlowError('hid_failed', 'lost reply')
    session = HidSession(Kvm(), tmp_path, None)
    async def scenario():
        with pytest.raises(FlowError):
            await session.click((1, 1, 2, 2), failure='failed', before_send=lambda: events.append('boundary'))
        assert events == ['handover', 'boundary', 'sent']
    run_async(scenario())


def test_inflight_same_key_pauses_and_other_key_fails_fast(monkeypatch):
    srv, kvm = setup_server(monkeypatch)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def launch(kvm, **kw):
            kw['on_start_hid']()
            kvm.events.append(('start',))
            entered.set()
            await release.wait()
            kvm.show('monitoring', ['TESTING'] * 4)
        srv._commands['start_round'].stations['DFU'] = launch
        first = asyncio.create_task(send(srv))
        await entered.wait()
        duplicate = await send(srv)
        conflict = await send(srv, req_id='another-key')
        assert duplicate['data'] == {'kind': 'paused'}
        assert conflict['error'] == 'device_busy'
        release.set()
        assert (await first)['data'] == {'kind': 'started'}
        assert kvm.events.count(('start',)) == 1
    run_async(scenario())


@pytest.mark.parametrize('station', ['DFU', 'BT'])
def test_full_real_flows_with_contract_frames_and_budget(tmp_path, station, monkeypatch):
    import time
    from dfu_helpers import SyntheticDfuKVM, BUTTON, save_templates
    from test_bt_start_test_1_0 import BtKvm, _bt_screen, _save_templates, START_ALL_BOX
    from protocol_helpers import render_log_solution, live_heartbeat
    from fake_kvm import make_snapshot, beside
    from hid_session import HidSession
    method_samples = {}
    for method in ('load', 'find'):
        original = getattr(HidSession, method)
        def measured(self, *a, _name=method, _fn=original, **kw):
            begin = time.monotonic()
            try:
                return _fn(self, *a, **kw)
            finally:
                method_samples.setdefault(_name, []).append(time.monotonic() - begin)
        monkeypatch.setattr(HidSession, method, measured)
    original_wait = HidSession.wait_fresh_frame
    async def measured_wait(self, *a, **kw):
        begin = time.monotonic()
        try:
            return await original_wait(self, *a, **kw)
        finally:
            method_samples.setdefault('wait_fresh_frame', []).append(time.monotonic() - begin)
    monkeypatch.setattr(HidSession, 'wait_fresh_frame', measured_wait)
    count = {'frames': 0, 'clicks': []}
    if station == 'DFU':
        class Kvm(SyntheticDfuKVM):
            async def click(self, x, y):
                begin = time.monotonic()
                await super().click(x, y)
                count['clicks'].append((x, y, time.monotonic() - begin))
                bx, by, bw, bh = BUTTON
                if bx <= x < bx + bw and by <= y < by + bh:
                    self.log_statuses = ['TESTING'] * 4
        kvm = Kvm([(40, 50), (120, 50), (40, 90), (120, 90)], log_state='preparing')
        save_templates(tmp_path, kvm.positions)
        original_latest = kvm.latest_frame
        def latest():
            count['frames'] += 1
            return original_latest()
        kvm.latest_frame = latest
    else:
        class Kvm(BtKvm):
            log_statuses = ['WAITING'] * 4
            async def click(self, x, y):
                begin = time.monotonic()
                await super().click(x, y)
                count['clicks'].append((x, y, time.monotonic() - begin))
                x0, y0, x1, y1 = START_ALL_BOX
                if x0 <= x < x1 and y0 <= y < y1:
                    self.log_statuses = ['TESTING'] * 4
            def latest_frame(self):
                count['frames'] += 1
                self._frame_seq += 1
                now = time.monotonic()
                app = render_log_solution(self.log_state, self.log_statuses, heartbeat=live_heartbeat(now))
                return make_snapshot(beside(self.frame, app), sequence=self._frame_seq,
                                     received_monotonic=now, stream_id='synthetic', pts=now)
        frame = _bt_screen()
        _save_templates(tmp_path, frame)
        kvm = Kvm(frame, log_state='preparing')
    srv = TcpJsonServer(device_registry=make_registry(), kvm_pool=make_log_solution_pool(kvm), template_root=tmp_path)
    stream = srv._slot(station + '-01').stream
    original_live = stream.wait_live
    async def measured_live(*a, **kw):
        begin = time.monotonic()
        try:
            return await original_live(*a, **kw)
        finally:
            method_samples.setdefault('freeze_precheck', []).append(time.monotonic() - begin)
    stream.wait_live = measured_live
    original_heartbeat = srv._heartbeat_refusal
    async def measured_heartbeat(*a, **kw):
        begin = time.monotonic()
        try:
            return await original_heartbeat(*a, **kw)
        finally:
            method_samples.setdefault('heartbeat_probe', []).append(time.monotonic() - begin)
    srv._heartbeat_refusal = measured_heartbeat
    async def scenario():
        device = station + '-01'
        task = asyncio.create_task(send(srv, device=device))
        while not srv._slot(device).round.start_requested:
            if task.done():
                pytest.fail(str(await task))
            await asyncio.sleep(.01)
        kvm.log_state = 'monitoring'
        result = await task
        assert result['data'] == {'kind': 'started'}, result
        assert len(count['clicks']) == 2  # Station Dock, then actual start button.
        assert not srv._slot(device).round.input_released
        record = srv._slot(device).start_round_records['round1']
        _write_evidence(f'213-budget-{station}.json', json.dumps({
            'source': 'full real monitor/start flows and template matching; synthetic contract 1.3 KVM',
            'counts': count, 'nested_method_samples_sec': method_samples,
            'note': 'nested samples are included in phase totals; do not sum them twice',
            'measurements': record.measurements}, indent=2))
    run_async(scenario())


def test_real_standby_monitor_sends_dock_and_shortcut_once(tmp_path):
    from test_monitor_start import _save_dock_template
    kvm = LogSolutionKVM('standby')
    _save_dock_template(tmp_path, kvm)
    srv = TcpJsonServer(device_registry=make_registry(), kvm_pool=make_log_solution_pool(kvm), template_root=tmp_path)
    async def launch(kvm, **kw):
        assert ('key', 'cmd+shift+m') in kvm.events
        kw['on_start_hid']()
        kvm.events.append(('start',))
        kvm.show('monitoring', ['TESTING'] * 4)
    srv._commands['start_round'].stations['DFU'] = launch
    result = run_async(send(srv))
    assert result['data'] == {'kind': 'started'}, result
    assert kvm.events.count(('key', 'cmd+shift+m')) == 1
    assert sum(e[0] == 'click' for e in kvm.events) == 1
    assert kvm.events.index(('key', 'cmd+shift+m')) < kvm.events.index(('start',))
    record = srv._slot('DFU-01').start_round_records['round1']
    _write_evidence('213-budget-log-dock.json', json.dumps({
        'source': 'real Log Solution Dock/shortcut initialization; synthetic KVM; launch stub',
        'events': kvm.events, 'measurements': record.measurements}, indent=2))
