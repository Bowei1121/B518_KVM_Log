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
    raise RuntimeError('fatal worker 中文')
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
    assert 'Fatal Core background task' in log.read_text(encoding='utf-8')
    assert 'Traceback (most recent call last)' in log.read_text(encoding='utf-8')
    assert json.loads(status.read_text())['detail'].isascii()


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


def test_core_bilingual_formatter_preserves_chinese_extra(tmp_path):
    from core_daemon import setup_rotating_logging
    log = tmp_path / 'bilingual.log'
    target = setup_rotating_logging(log, console=False, logger_name='test204')
    try:
        target.error('Worker failed: %s', ascii(RuntimeError('錯誤')), extra={'zh': '中文：%s'})
        _, english, chinese = log.read_text(encoding='utf-8').strip().split(' | ')
        assert english.isascii()
        assert english.startswith('Worker failed: ')
        assert chinese == '中文：' + ascii(RuntimeError('錯誤'))
    finally:
        for handler in list(target.handlers):
            handler.close()
            target.removeHandler(handler)


def test_loop_transport_exception_does_not_invoke_core_fatal(monkeypatch):
    from unittest.mock import AsyncMock
    from tcp_server import TcpJsonServer

    async def scenario():
        server = TcpJsonServer(port=0)
        server.on_fatal = Mock()
        listener = Mock(sockets=[])
        listener.wait_closed = AsyncMock()
        monkeypatch.setattr(asyncio, 'start_server', AsyncMock(return_value=listener))
        server.kvm_pool.start = AsyncMock()
        server.kvm_pool.close_all = AsyncMock()
        def started():
            asyncio.get_running_loop().call_exception_handler(
                {'message': 'x', 'exception': ConnectionResetError()})
            server._shutdown_event.set()
        server.on_started = started
        await server._run_server_async()
        server.on_fatal.assert_not_called()
    asyncio.run(scenario())


def test_default_watchdog_log_is_separate(monkeypatch, tmp_path):
    import core_daemon as daemon
    for frozen in (False, True):
        monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
        monkeypatch.setattr(daemon, '_user_log_dir', lambda: tmp_path)
        assert daemon.default_watchdog_log_file() != daemon.default_log_file()
        assert daemon.default_watchdog_log_file().parent == daemon.default_log_file().parent
        assert daemon.parse_args(['watchdog']).watchdog_log_file == str(daemon.default_watchdog_log_file())


def test_watchdog_main_separates_parent_and_child_logs(monkeypatch, tmp_path):
    import pytest
    import core_daemon as daemon
    core_log = str(tmp_path / 'core.log')
    watchdog_log = str(tmp_path / 'watchdog.log')
    args = daemon.parse_args(['watchdog', '--log-file', core_log,
                              '--watchdog-log-file', watchdog_log])
    setup = Mock()
    launch = Mock(return_value=['child'])
    monkeypatch.setattr(daemon, 'setup_rotating_logging', setup)
    monkeypatch.setattr(daemon, 'core_launch_command', launch)
    monkeypatch.setattr(daemon, 'watch_core', Mock(return_value=0))
    with pytest.raises(SystemExit) as exc:
        daemon.main(args)
    assert exc.value.code == 0
    setup.assert_called_once_with(watchdog_log, args.log_level, args.backup_count, console=False)
    child_args = launch.call_args.args[0]
    assert child_args[child_args.index('--log-file') + 1] == core_log
    assert '--watchdog-log-file' not in child_args
