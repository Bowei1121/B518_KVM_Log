"""Fail-fast supervision and bounded restart policy."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host-app'))


def test_task_failure_invokes_fatal_callback():
    from task_supervisor import supervise_task

    async def scenario():
        fatal = Mock()
        async def broken():
            raise RuntimeError('worker failed')
        task = supervise_task(asyncio.create_task(broken()), fatal)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert fatal.call_count == 1
        assert isinstance(fatal.call_args.args[0], RuntimeError)
    asyncio.run(scenario())


def test_cancelled_and_successful_tasks_are_not_fatal():
    from task_supervisor import supervise_task

    async def scenario():
        fatal = Mock()
        task = supervise_task(asyncio.create_task(asyncio.sleep(100)), fatal)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await supervise_task(asyncio.create_task(asyncio.sleep(0)), fatal)
        await asyncio.sleep(0)
        fatal.assert_not_called()
    asyncio.run(scenario())


def test_watchdog_backoff_and_limit():
    from core_daemon import watch_core
    delays = []
    spawn = Mock(return_value=Mock(wait=Mock(return_value=1)))
    assert watch_core(['core', 'run'], spawn=spawn, sleep=delays.append,
                      clock=lambda: 0, max_restarts=3, window_sec=600,
                      backoff_sec=2, cooldown_sec=60) == 1
    assert spawn.call_count == 4
    assert delays == [2, 4, 8]


def test_watchdog_clean_exit_never_restarts():
    from core_daemon import watch_core
    spawn = Mock(return_value=Mock(wait=Mock(return_value=0)))
    sleep = Mock()
    assert watch_core(['core', 'run'], spawn=spawn, sleep=sleep) == 0
    assert spawn.call_count == 1
    sleep.assert_not_called()


def test_core_background_failure_exits_nonzero_and_records_status(tmp_path):
    import json
    import subprocess
    devices = tmp_path / 'devices.json'
    devices.write_text(json.dumps({'DFU-01': {'station': 'DFU',
        'kvm_ip': '192.0.2.1', 'channels': 4}}))
    status = tmp_path / 'status.json'
    log = tmp_path / 'core.log'
    script = '''
import asyncio, sys
sys.path.insert(0, 'host-app')
from kvm_pool import KVMConnectionPool
from core_daemon import run_service_foreground
async def broken(self):
    await asyncio.sleep(0.01)
    raise RuntimeError('fatal worker')
KVMConnectionPool._maintenance_worker = broken
run_service_foreground(port=0, devices_file=sys.argv[1], status_file=sys.argv[2],
                       log_file=sys.argv[3], pid_file=sys.argv[4], console=False)
'''
    result = subprocess.run([sys.executable, '-c', script, str(devices), str(status),
                             str(log), str(tmp_path / 'core.pid')],
                            cwd=Path(__file__).resolve().parents[1], timeout=10)
    assert result.returncode == 1
    assert json.loads(status.read_text())['state'] == 'failed'
    assert 'fatal worker' in json.loads(status.read_text())['detail']
    assert 'Fatal Core background task' in log.read_text()
    assert log.read_text().isascii()


def test_watchdog_window_expires_and_delay_is_capped():
    from core_daemon import watch_core
    now = [0]
    delays = []
    def sleep(delay):
        delays.append(delay)
        now[0] += delay
    exits = iter([1, 1, 1, 0])
    def spawn(command):
        now[0] += 20
        return Mock(wait=lambda: next(exits))
    assert watch_core(['core'], spawn=spawn, sleep=sleep, clock=lambda: now[0],
                      max_restarts=1, window_sec=10, backoff_sec=20, cooldown_sec=5) == 0
    assert delays == [5, 5, 5]


def test_core_ascii_formatter_ignores_chinese_extra_and_escapes_error(tmp_path):
    import logging
    from core_daemon import setup_rotating_logging
    log = tmp_path / 'ascii.log'
    target = setup_rotating_logging(log, console=False, logger_name='test204')
    try:
        target.error('Worker failed: %s', '錯誤', extra={'zh': '中文'})
        assert log.read_text().isascii()
    finally:
        for handler in list(target.handlers):
            handler.close()
            target.removeHandler(handler)
