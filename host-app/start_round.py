"""Single-device start orchestration. Records live only for this Core process."""
import asyncio
import copy
import time
from dataclasses import dataclass, field
from contextlib import contextmanager

from hid_session import FlowError
from log_solution_reader import MarkerState
from round_check import Reading


def reply(code='', message='', kind='fault', status='error'):
    return dict(status=status, error=code, message=message.encode('ascii', 'replace').decode(), data={'kind': kind})


@dataclass
class Record:
    timeout: float
    deadline: float
    accepted: bool = False
    sent: bool = False
    last_error: tuple = None
    # Conservative retry barrier for unclassified preparatory HID; not a reply kind.
    side_effect: bool = False
    result: dict = None
    measurements: list = field(default_factory=list)

    @contextmanager
    def measure(self, stage):
        begin = time.monotonic()
        try:
            yield
        finally:
            self.measurements.append((stage, time.monotonic() - begin, self.deadline - time.monotonic()))

    def mark_sent(self):
        # Before awaiting the RPC: loss of its reply must never permit a second click.
        self.sent = True


def uncertain(code='hid_failed', message='HID outcome requires review'):
    return reply(code, message, 'paused')


async def dispatch(server, req_id, admitted, reader, writer):
    from tcp_server import CommandContext, _HandoverKvm
    entry = admitted.entry
    slot = server._slot(entry.device_id)
    records = slot.start_round_records
    if req_id in records:
        rec = records[req_id]
        if rec.result and rec.result['data']['kind'] == 'started' and rec.timeout == admitted.args:
            return copy.deepcopy(rec.result)
        if rec.result and rec.result.get('error'):
            return uncertain(rec.result['error'], rec.result['message'])
        return uncertain()  # Undefined resume/cache policy: never repeat side effects.
    if any(r.accepted and r.result and r.result['data']['kind'] != 'started' for r in records.values()):
        return uncertain(message='Start round blocked: this req_id cannot start a new round')
    if not server.lock_manager.try_acquire(entry.device_id, holder='start_round'):
        return reply('device_busy', 'Device is busy')
    rec = Record(admitted.args, time.monotonic() + admitted.args + 73)
    records[req_id] = rec
    leased = False
    raw = None
    async def lifecycle():
        nonlocal leased, raw
        with rec.measure("connection"):
            raw = await server.kvm_pool.acquire_connection(entry.kvm_ip)
            leased = True
        wrapped = _HandoverKvm(raw, lambda: server._neutralize_manual_hid_async(entry.device_id, raw))
        ctx = CommandContext(entry, wrapped, slot, admitted.spec.stations[entry.station], reader, writer)
        return await server._handle_start_round(ctx, rec)
    deadline_timeout = asyncio.timeout(max(0, rec.deadline - time.monotonic()))
    try:
        # No per-step timeout resets. Pool cancellation closes its half-built client.
        async with deadline_timeout:
            rec.result = await lifecycle()
    except asyncio.TimeoutError:
        if not deadline_timeout.expired():
            code = 'kvm_connect_failed' if raw is None else 'internal_error'
            rec.result = uncertain(code, 'Start round failed') if rec.sent else reply(code, 'Start round failed')
        elif rec.last_error:
            rec.result = uncertain(*rec.last_error) if rec.sent else reply(*rec.last_error)
        else:
            rec.result = uncertain() if rec.sent else reply('timeout', 'Start round deadline expired', status='timeout')
    except asyncio.CancelledError:
        rec.result = uncertain()
        raise
    except Exception:
        code = 'kvm_connect_failed' if raw is None else 'internal_error'
        rec.result = uncertain(code, 'Start round failed') if rec.sent else reply(code, 'Start round failed')
    finally:
        try:
            if raw is not None:
                try:
                    with rec.measure("manual_reconcile"):
                        await asyncio.wait_for(server._release_dropped_manual(entry.device_id, raw), 1)
                except Exception:
                    rec.result = uncertain() if rec.sent else reply('hid_failed', 'HID cleanup failed')
            if leased:
                try:
                    with rec.measure("lease_release"):
                        await asyncio.wait_for(server.kvm_pool.release_connection(entry.kvm_ip), 1)
                except Exception:
                    rec.result = uncertain() if rec.sent else reply('kvm_connect_failed', 'KVM cleanup failed')
        finally:
            server.lock_manager.release(entry.device_id)
    return rec.result


async def read(kvm, gate):
    # Only the copy operation runs in a worker. Late reads cannot mutate a subsequent round.
    snapshot = await asyncio.to_thread(kvm.latest_frame)
    verdict = gate.stream.observe_snapshot(snapshot, gate.clock())
    if snapshot is None:
        return gate.decide(verdict, None, None, None)
    return gate.decide(verdict, snapshot.image, snapshot.sequence, snapshot.received_monotonic,
                       stream_id=snapshot.stream_id)


def check_health(kvm, gate, decision):
    if not kvm.is_healthy():
        gate.stream.release_kvm_lost_latch(decision.verdict)
        raise FlowError('kvm_connect_failed', 'KVM connection lost')
    if decision.kind == 'frozen':
        gate.stream.mark_frozen_delivered()
        raise FlowError('frame_frozen', 'Log Solution frame stream is frozen')
    if decision.kind == 'heartbeat_stopped':
        raise FlowError(decision.reason, decision.detail)
    if decision.kind == 'paused' or decision.state == MarkerState.REVIEW:
        raise FlowError('log_solution_review', 'Log Solution requires review')


async def run(server, ctx, rec):
    from monitor_flow import run_monitor_start
    slot = ctx.slot
    try:
        with rec.measure("picture_precheck"):
            refusal = await server._picture_refusal(ctx)
        if refusal:
            raise FlowError(*refusal)
        def accept():
            if not rec.accepted:
                slot.round.begin_start()
                rec.accepted = True
        # Only structured no-frame failures before any HID can re-enter initialization.
        class MonitorKvm:
            def __getattr__(self, name):
                value = getattr(ctx.kvm, name)
                if name in {'click', 'move', 'press_command_shift_m', 'type_text', 'press_enter'}:
                    async def effect(*a, **kw):
                        rec.side_effect = True
                        return await value(*a, **kw)
                    return effect
                return value
        retry_end = min(rec.deadline, time.monotonic() + rec.timeout)
        retry_begin = None
        for attempt in range(3):
            try:
                with rec.measure("monitor_initialize"):
                    await run_monitor_start(MonitorKvm(), slot.gate, ctx.entry.device_id,
                                        template_root=server._template_root(),
                                        confirm_sec=server.monitor_confirm_sec,
                                        on_accept=accept, on_shortcut=slot.round.note_start,
                                        on_preparing=slot.round.note_start, observe_only=True)
                break
            except FlowError as exc:
                rec.last_error = (exc.code, str(exc))
                if (exc.code != 'hid_failed' or exc.reason != 'no_frame' or rec.side_effect
                        or attempt == 2 or time.monotonic() + .5 >= retry_end):
                    raise
                if retry_begin is None:
                    retry_begin = time.monotonic()
                await asyncio.sleep(.5)
        rec.last_error = None
        monitor_spent = 0 if retry_begin is None else time.monotonic() - retry_begin
        monitor_end = min(rec.deadline, time.monotonic() + max(0, rec.timeout - monitor_spent))
        ready = False
        for attempt in range(3):
            begin = time.monotonic()
            end = monitor_end if attempt == 2 else min(monitor_end, begin + rec.timeout / 3)
            while time.monotonic() < end:
                decision = await read(ctx.kvm, slot.gate)
                if time.monotonic() >= end:
                    break
                check_health(ctx.kvm, slot.gate, decision)
                if decision.state == MarkerState.MONITORING:
                    ready = True
                    break
                await asyncio.sleep(min(.05, max(0, end - time.monotonic())))
            rec.measurements.append(('monitor_poll', time.monotonic() - begin, rec.deadline - time.monotonic()))
            if ready:
                break
            await asyncio.sleep(min(.5, max(0, monitor_end - time.monotonic())))
        if not ready:
            raise FlowError('monitor_not_started', 'Monitoring not observed within timeout')
        for attempt in range(3):
            refusal = await asyncio.to_thread(server._monitor_refusal, ctx.kvm, slot.stats)
            if refusal is None:
                rec.last_error = None
                break
            rec.last_error = refusal
            if refusal[0] != 'monitor_not_ready' or attempt == 2:
                raise FlowError(*refusal)
            await asyncio.sleep(.5)
        for attempt in range(3):
            try:
                with rec.measure("start_hid_flow"):
                    await ctx.operation(MonitorKvm(), template_root=server._template_root(),
                                        diagnostics_key=ctx.entry.device_id, on_start_hid=rec.mark_sent)
                break
            except FlowError as exc:
                rec.last_error = (exc.code, str(exc))
                if (exc.code != 'hid_failed' or exc.reason != 'no_frame' or rec.side_effect
                        or rec.sent or attempt == 2 or time.monotonic() + .5 >= rec.deadline):
                    raise
                await asyncio.sleep(.5)
        rec.last_error = None
        # Reuse check_start's evidence and same-round judgement on a private round copy.
        # A complete result may be judged here, but delivery/input release remain untouched.
        with rec.measure("start_confirmation"):
            gate = copy.copy(slot.gate)
            gate.round = copy.deepcopy(slot.round)
            gate.round.activity.reset()
            while time.monotonic() < rec.deadline:
                decision = await read(ctx.kvm, gate)
                if time.monotonic() >= rec.deadline:
                    return uncertain()
                check_health(ctx.kvm, gate, decision)
                reading = Reading.from_decision(decision, gate.last_active)
                if reading.active or decision.kind == 'taken':
                    return reply(kind='started', status='ok')
                await asyncio.sleep(min(.05, max(0, rec.deadline - time.monotonic())))
            return uncertain()
    except FlowError as exc:
        if exc.code == 'log_solution_review' or rec.sent or exc.code == 'log_solution_busy':
            return uncertain(exc.code, str(exc))
        return reply(exc.code, str(exc))
