"""Tests for Core Daemon, rotating logging, and TE process management utilities."""

from __future__ import annotations

import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

import pytest

# Ensure host-app and tools are on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_APP_DIR = _REPO_ROOT / "host-app"
_TOOLS_DIR = _REPO_ROOT / "tools"

for p in (str(_HOST_APP_DIR), str(_TOOLS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core_daemon import (
    find_pids_on_port,
    force_kill_core_service,
    graceful_stop_core_service,
    is_pid_alive,
    is_port_free,
    query_status,
    read_pid_file,
    setup_rotating_logging,
    start_core_daemon,
    write_pid_file,
)


def get_free_port() -> int:
    """Acquire an ephemeral port available for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_headless_import_and_no_tkinter_dependency():
    """Verify core_daemon has no dependency on DISPLAY or Tkinter."""
    code = (
        "import sys\n"
        "sys.modules['tkinter'] = None\n"
        "import os\n"
        "os.environ.pop('DISPLAY', None)\n"
        f"sys.path.insert(0, {str(_HOST_APP_DIR)!r})\n"
        "import core_daemon\n"
        "print('HEADLESS_SUCCESS')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"Failed: {res.stderr}"
    assert "HEADLESS_SUCCESS" in res.stdout


def test_rotating_file_logging(tmp_path: Path):
    """Verify rotating log files record timestamps, levels, PIDs and rotate properly."""
    log_file = tmp_path / "test_service.log"
    logger = setup_rotating_logging(
        log_file=log_file,
        log_level="DEBUG",
        max_bytes=350,  # Small size to trigger fast rotation
        backup_count=3,
        console=False,
        logger_name="TestRotatingLogger",
    )

    # Write log entries
    for i in range(15):
        logger.info(f"Test log entry iteration number {i} - long message payload padding")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")

    # Verify log format: timestamp, log level, logger name, PID
    # e.g.: 2026-10-06 17:40:00 [INFO] [TestRotatingLogger] [PID:12345]: Test log entry ...
    pattern = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(INFO|DEBUG|WARNING|ERROR)\] \[TestRotatingLogger\] \[PID:\d+\]: Test log entry",
        re.MULTILINE,
    )
    assert pattern.search(content) is not None, f"Pattern not found in log content:\n{content}"

    # Verify that rotation occurred and backup file exists
    backup_file = tmp_path / "test_service.log.1"
    assert backup_file.exists(), "Log rotation did not produce expected .1 backup file"


def test_start_status_and_graceful_stop_lifecycle(tmp_path: Path):
    """Verify start, status querying, and graceful shutdown lifecycle."""
    port = get_free_port()
    log_file = tmp_path / "core.log"
    pid_file = tmp_path / "core.pid"

    # 1. Start daemon
    start_res = start_core_daemon(
        host="127.0.0.1",
        port=port,
        log_file=log_file,
        pid_file=pid_file,
        timeout=10.0,
    )
    assert start_res["status"] == "ok"
    pid = start_res["pid"]
    assert pid > 0
    assert is_pid_alive(pid)
    assert not is_port_free(port)

    # Verify double-start reports already_running
    double_start = start_core_daemon(
        host="127.0.0.1",
        port=port,
        log_file=log_file,
        pid_file=pid_file,
        timeout=3.0,
    )
    assert double_start["status"] == "already_running"

    # 2. Query status
    status_res = query_status(
        host="127.0.0.1",
        port=port,
        pid_file=pid_file,
        timeout=2.0,
    )
    assert status_res["status"] == "running"
    assert status_res["pid"] == pid
    details = status_res.get("details", {})
    assert details.get("status") == "running"
    assert "uptime_seconds" in details
    assert "kvm_pool" in details
    assert "freeze_guard" in details

    # 3. Graceful stop
    stop_res = graceful_stop_core_service(
        host="127.0.0.1",
        port=port,
        pid_file=pid_file,
        timeout=5.0,
    )
    assert stop_res["status"] == "ok"
    assert is_port_free(port)
    assert not is_pid_alive(pid)

    # 4. Status after stop
    stopped_status = query_status(
        host="127.0.0.1",
        port=port,
        pid_file=pid_file,
        timeout=2.0,
    )
    assert stopped_status["status"] == "stopped"


def test_force_kill_releases_port_and_cleans_up_orphaned_process(tmp_path: Path):
    """Verify force-kill terminates stubborn/orphaned processes and frees ports."""
    port = get_free_port()
    pid_file = tmp_path / "hung.pid"

    # Launch a mock process holding the port and ignoring SIGTERM
    script = (
        "import socket, time, signal, sys\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port}))\n"
        "s.listen(5)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"  # Ignore SIGTERM to simulate hung process
        "print('READY', flush=True)\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait until hung server binds the port
        line = proc.stdout.readline()
        assert "READY" in line
        assert not is_port_free(port)

        write_pid_file(pid_file, proc.pid)
        assert is_pid_alive(proc.pid)

        # Execute aggressive force-kill
        kill_res = force_kill_core_service(
            port=port,
            pid_file=pid_file,
            host="127.0.0.1",
            timeout=5.0,
        )

        assert kill_res["status"] == "ok"
        assert kill_res["port_released"] is True
        assert proc.pid in kill_res["killed_pids"]

        # Confirm process is dead and port is free
        assert not is_pid_alive(proc.pid)
        assert is_port_free(port)
        assert not pid_file.exists()

    finally:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass


def test_manage_core_cli_tool(tmp_path: Path):
    """Verify tools/manage_core.py CLI tool handles start, status, stop, force-kill."""
    port = get_free_port()
    log_file = tmp_path / "cli.log"
    pid_file = tmp_path / "cli.pid"

    manage_script = _TOOLS_DIR / "manage_core.py"

    # 1. Start via CLI
    start_cmd = [
        sys.executable,
        str(manage_script),
        "start",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-file", str(log_file),
        "--pid-file", str(pid_file),
        "--timeout", "10.0",
    ]
    res_start = subprocess.run(start_cmd, capture_output=True, text=True, timeout=15)
    assert res_start.returncode == 0, f"Start failed: {res_start.stderr}"
    assert "[OK]" in res_start.stdout

    try:
        # 2. Status via CLI
        status_cmd = [
            sys.executable,
            str(manage_script),
            "status",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--pid-file", str(pid_file),
        ]
        res_status = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
        assert res_status.returncode == 0
        assert "RUNNING" in res_status.stdout
        assert f"127.0.0.1:{port}" in res_status.stdout

        # 3. Stop via CLI
        stop_cmd = [
            sys.executable,
            str(manage_script),
            "stop",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--pid-file", str(pid_file),
            "--timeout", "5.0",
        ]
        res_stop = subprocess.run(stop_cmd, capture_output=True, text=True, timeout=10)
        assert res_stop.returncode == 0
        assert "[OK]" in res_stop.stdout
        assert is_port_free(port)

        # 4. Status after stop
        res_status_after = subprocess.run(status_cmd, capture_output=True, text=True, timeout=10)
        assert res_status_after.returncode == 0
        assert "STOPPED" in res_status_after.stdout

    finally:
        # Emergency cleanup if test failed mid-way
        if not is_port_free(port):
            force_kill_core_service(port=port, pid_file=pid_file)


def test_restart_core_daemon(tmp_path: Path):
    """Verify restart_core_daemon cleanly stops existing instance and starts a new one."""
    port = get_free_port()
    log_file = tmp_path / "restart.log"
    pid_file = tmp_path / "restart.pid"

    # Start initially
    res_start = start_core_daemon(
        host="127.0.0.1",
        port=port,
        log_file=log_file,
        pid_file=pid_file,
        timeout=10.0,
    )
    assert res_start["status"] == "ok"
    old_pid = res_start["pid"]

    try:
        # Restart
        res_restart = start_core_daemon.__globals__["restart_core_daemon"](
            host="127.0.0.1",
            port=port,
            log_file=log_file,
            pid_file=pid_file,
            timeout=10.0,
        )
        assert res_restart["status"] == "ok"
        new_pid = res_restart["pid"]
        assert is_pid_alive(new_pid)
        assert not is_port_free(port)

        # Stop
        stop_res = graceful_stop_core_service(
            host="127.0.0.1",
            port=port,
            pid_file=pid_file,
            timeout=5.0,
        )
        assert stop_res["status"] == "ok"
        assert is_port_free(port)
    finally:
        if not is_port_free(port):
            force_kill_core_service(port=port, pid_file=pid_file)


def test_unresponsive_status_detection(tmp_path: Path):
    """Verify query_status detects when process exists but is unresponsive to TCP."""
    port = get_free_port()
    pid_file = tmp_path / "unresponsive.pid"

    # Spawn a dummy process that does NOT bind the TCP port
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"]
    )
    try:
        write_pid_file(pid_file, proc.pid)
        status_res = query_status(
            host="127.0.0.1",
            port=port,
            pid_file=pid_file,
            timeout=1.0,
        )
        assert status_res["status"] == "unresponsive"
        assert status_res["pid"] == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=1.0)


def test_manage_core_cli_force_kill(tmp_path: Path):
    """Verify tools/manage_core.py force-kill subcommand cleans up stuck process."""
    port = get_free_port()
    pid_file = tmp_path / "cli_kill.pid"
    manage_script = _TOOLS_DIR / "manage_core.py"

    # Spawn process occupying port
    script = (
        "import socket, time\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port}))\n"
        "s.listen(5)\n"
        "print('PORT_BUSY', flush=True)\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        proc.stdout.readline()
        assert not is_port_free(port)
        write_pid_file(pid_file, proc.pid)

        # Execute force-kill via CLI
        kill_cmd = [
            sys.executable,
            str(manage_script),
            "force-kill",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--pid-file", str(pid_file),
            "--timeout", "5.0",
        ]
        res_kill = subprocess.run(kill_cmd, capture_output=True, text=True, timeout=10)
        assert res_kill.returncode == 0
        assert "RELEASED (FREE)" in res_kill.stdout
        assert is_port_free(port)
    finally:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass
