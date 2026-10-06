"""Core Daemon & Process Management Utilities for B518 JetKVM Relay.

Provides headless execution, rotating file logging, process lifecycle
management (start, stop, restart, status), and aggressive recovery (force-kill).
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Union
import uuid

# Ensure host-app directory is on sys.path
_HOST_APP_DIR = Path(__file__).resolve().parent
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

# Ensure repo root is discoverable
_REPO_ROOT = _HOST_APP_DIR.parent
_DEFAULT_LOG_FILE = _REPO_ROOT / "logs" / "core_service.log"
_DEFAULT_PID_FILE = _REPO_ROOT / "run" / "core_service.pid"

try:
    from tcp_server import TcpJsonServer
except ImportError:
    from .tcp_server import TcpJsonServer  # type: ignore[no-redef]

logger = logging.getLogger("CoreDaemon")


def setup_rotating_logging(
    log_file: Optional[Union[str, Path]] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
    logger_name: Optional[str] = None,
) -> logging.Logger:
    """Configure rotating file logging with timestamp and log levels.

    Args:
        log_file: File path for the rotating log file.
        log_level: Logging level string (e.g. 'DEBUG', 'INFO', 'WARNING', 'ERROR').
        max_bytes: Maximum file size before rotation occurs.
        backup_count: Number of rotated backup files to retain.
        console: If True, also stream formatted logs to sys.stdout.
        logger_name: Target logger name (None for root logger).

    Returns:
        The configured Logger instance.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] [PID:%(process)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_file is not None:
        log_path = Path(log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Avoid duplicate RotatingFileHandler on same file
        existing_rfh = any(
            isinstance(h, RotatingFileHandler)
            and getattr(h, "baseFilename", None) == str(log_path)
            for h in target_logger.handlers
        )
        if not existing_rfh:
            rfh = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            rfh.setLevel(level)
            rfh.setFormatter(formatter)
            target_logger.addHandler(rfh)

    if console:
        existing_console = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
            for h in target_logger.handlers
        )
        if not existing_console:
            sh = logging.StreamHandler(sys.stdout)
            sh.setLevel(level)
            sh.setFormatter(formatter)
            target_logger.addHandler(sh)

    return target_logger


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID exists, is active, and is not a zombie."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False

    # Check if direct child that has already exited or is a zombie
    if sys.platform != "win32":
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                return False
        except (ChildProcessError, OSError):
            pass

        try:
            out = subprocess.check_output(
                ["ps", "-o", "stat=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if not out or "Z" in out:
                return False
        except Exception:
            return False

    return True


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently free to bind."""
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((check_host, port))
            return True
        except OSError:
            return False


def find_pids_on_port(port: int) -> List[int]:
    """Find all PIDs listening on or bound to the specified TCP port."""
    pids: Set[int] = set()

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "tcp"],
                text=True,
                errors="replace",
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and parts[0].upper() == "TCP":
                    local_addr = parts[1]
                    state = parts[3].upper()
                    if state == "LISTENING" and (
                        local_addr.endswith(f":{port}") or f":{port}" in local_addr
                    ):
                        try:
                            pids.add(int(parts[4]))
                        except ValueError:
                            pass
        except Exception as exc:
            logger.warning("Failed to inspect netstat on Windows: %s", exc)
    else:
        # macOS / Linux: use lsof
        try:
            cmd = ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            for line in out.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
        except subprocess.CalledProcessError:
            pass
        except FileNotFoundError:
            # Fallback to fuser if lsof is missing
            try:
                cmd = ["fuser", f"{port}/tcp"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                for part in out.strip().split():
                    if part.isdigit():
                        pids.add(int(part))
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to query port using lsof: %s", exc)

    return sorted(pids)


def read_pid_file(pid_file: Union[str, Path]) -> Optional[int]:
    """Read recorded PID from file if present and valid."""
    path = Path(pid_file)
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        return int(content) if content.isdigit() else None
    except Exception:
        return None


def write_pid_file(pid_file: Union[str, Path], pid: int) -> None:
    """Record current PID into specified pid file."""
    path = Path(pid_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def remove_pid_file(pid_file: Union[str, Path]) -> None:
    """Safely remove pid file."""
    path = Path(pid_file)
    try:
        if path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        pass


def send_tcp_command(
    cmd: str,
    payload: Optional[Dict[str, Any]] = None,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Send a command over TCP and return parsed JSON response."""
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    req: Dict[str, Any] = {"req_id": f"mgmt-{uuid.uuid4().hex[:8]}", "cmd": cmd}
    if payload:
        req.update(payload)

    with socket.create_connection((check_host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        data = (json.dumps(req) + "\n").encode("utf-8")
        sock.sendall(data)

        buf = bytearray()
        while b"\n" not in buf:
            chunk = sock.recv(2048)
            if not chunk:
                break
            buf.extend(chunk)

        line = buf.split(b"\n")[0]
        if not line:
            raise ConnectionError("Empty response received from Core Service")
        return json.loads(line.decode("utf-8"))


def query_status(
    host: str = "127.0.0.1",
    port: int = 5000,
    pid_file: Optional[Union[str, Path]] = None,
    timeout: float = 2.0,
) -> Dict[str, Any]:
    """Query Core Service status via TCP and inspect PID file."""
    effective_pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE
    pid = read_pid_file(effective_pid_file)
    is_alive = is_pid_alive(pid) if pid else False

    # Attempt TCP status request
    try:
        resp = send_tcp_command("status", host=host, port=port, timeout=timeout)
        if resp.get("status") == "ok":
            return {
                "status": "running",
                "pid": pid,
                "host": host,
                "port": port,
                "details": resp.get("data", {}),
            }
        return {
            "status": "running",
            "pid": pid,
            "host": host,
            "port": port,
            "details": resp,
        }
    except Exception as exc:
        if is_alive:
            return {
                "status": "unresponsive",
                "pid": pid,
                "host": host,
                "port": port,
                "message": f"Process {pid} is alive but not answering TCP query: {exc}",
            }
        return {
            "status": "stopped",
            "pid": pid,
            "host": host,
            "port": port,
            "message": "Core service is not running",
        }


def graceful_stop_core_service(
    host: str = "127.0.0.1",
    port: int = 5000,
    pid_file: Optional[Union[str, Path]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Trigger graceful shutdown via TCP shutdown command and confirm process termination."""
    effective_pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE
    pid = read_pid_file(effective_pid_file)

    # 1. Send shutdown command
    shutdown_sent = False
    try:
        resp = send_tcp_command("shutdown", host=host, port=port, timeout=min(3.0, timeout))
        if resp.get("status") == "ok":
            shutdown_sent = True
    except (ConnectionRefusedError, socket.timeout, ConnectionError):
        # Service might already be stopped
        pass
    except Exception as exc:
        logger.warning("Shutdown command failed: %s", exc)

    # 2. Confirm termination
    deadline = time.time() + timeout
    terminated = False

    while time.time() < deadline:
        alive = is_pid_alive(pid) if pid else False
        port_free = is_port_free(port, host)
        if not alive and port_free:
            terminated = True
            break
        time.sleep(0.1)

    if effective_pid_file:
        remove_pid_file(effective_pid_file)

    if terminated:
        return {
            "status": "ok",
            "pid": pid,
            "port": port,
            "message": f"Core service (PID: {pid}) terminated gracefully.",
        }

    return {
        "status": "timeout",
        "pid": pid,
        "port": port,
        "shutdown_sent": shutdown_sent,
        "message": f"Process {pid} did not terminate within {timeout}s after shutdown command.",
    }


def force_kill_core_service(
    port: int = 5000,
    pid_file: Optional[Union[str, Path]] = None,
    host: str = "127.0.0.1",
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Aggressively recover from hung/zombie states by terminating lingering processes and releasing ports."""
    effective_pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE
    target_pids: Set[int] = set()

    # 1. Read PID from pidfile if present
    pid_from_file = read_pid_file(effective_pid_file)
    if pid_from_file and is_pid_alive(pid_from_file):
        target_pids.add(pid_from_file)

    # 2. Find any processes binding or listening on the port
    for p in find_pids_on_port(port):
        target_pids.add(p)

    current_pid = os.getpid()
    target_pids.discard(current_pid)

    killed_pids: List[int] = []

    # 3. Aggressively kill targets
    for pid in target_pids:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/T"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            killed_pids.append(pid)
        except (ProcessLookupError, OSError):
            pass

    # 4. Confirm port is released
    deadline = time.time() + timeout
    port_freed = is_port_free(port, host)
    while not port_freed and time.time() < deadline:
        time.sleep(0.1)
        port_freed = is_port_free(port, host)

    # 5. Clean up pid file
    remove_pid_file(effective_pid_file)

    return {
        "status": "ok" if port_freed else "error",
        "killed_pids": killed_pids,
        "port_released": port_freed,
        "port": port,
        "message": (
            f"Killed {len(killed_pids)} process(es); "
            f"port {port} {'successfully released' if port_freed else 'remains occupied'}."
        ),
    }


def start_core_daemon(
    host: str = "0.0.0.0",
    port: int = 5000,
    log_file: Optional[Union[str, Path]] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    pid_file: Optional[Union[str, Path]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Start Core Daemon as a headless background process and wait until responsive."""
    effective_log_file = Path(log_file) if log_file else _DEFAULT_LOG_FILE
    effective_pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE
    check_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host

    # Check if already running
    existing_pid = read_pid_file(effective_pid_file)
    if existing_pid and is_pid_alive(existing_pid):
        try:
            resp = send_tcp_command("ping", host=check_host, port=port, timeout=1.0)
            if resp.get("status") == "ok":
                return {
                    "status": "already_running",
                    "pid": existing_pid,
                    "host": host,
                    "port": port,
                    "message": f"Core daemon is already active (PID: {existing_pid}, Port: {port})",
                }
        except Exception:
            pass

    if not is_port_free(port, check_host):
        raise RuntimeError(f"Cannot start Core Daemon: Port {port} is already in use.")

    script_path = Path(__file__).resolve()
    cmd = [
        sys.executable,
        str(script_path),
        "run",
        "--host", host,
        "--port", str(port),
        "--log-file", str(effective_log_file),
        "--log-level", log_level,
        "--max-bytes", str(max_bytes),
        "--backup-count", str(backup_count),
        "--pid-file", str(effective_pid_file),
    ]

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)

    # Poll ping until healthy or timeout
    deadline = time.time() + timeout
    started = False
    last_err: Optional[Exception] = None

    while time.time() < deadline:
        time.sleep(0.15)
        try:
            resp = send_tcp_command("ping", host=check_host, port=port, timeout=1.0)
            if resp.get("status") == "ok":
                started = True
                break
        except Exception as exc:
            last_err = exc

    if not started:
        if not is_pid_alive(proc.pid):
            raise RuntimeError(
                f"Core daemon process {proc.pid} exited prematurely during startup."
            )
        raise TimeoutError(f"Core daemon did not respond within {timeout}s: {last_err}")

    recorded_pid = read_pid_file(effective_pid_file) or proc.pid
    return {
        "status": "ok",
        "pid": recorded_pid,
        "host": host,
        "port": port,
        "log_file": str(effective_log_file),
        "message": f"Core daemon started successfully (PID: {recorded_pid}, Port: {port}).",
    }


def restart_core_daemon(
    host: str = "0.0.0.0",
    port: int = 5000,
    log_file: Optional[Union[str, Path]] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    pid_file: Optional[Union[str, Path]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Gracefully restart Core Daemon."""
    stop_res = graceful_stop_core_service(
        host=host, port=port, pid_file=pid_file, timeout=timeout
    )
    if stop_res["status"] != "ok":
        # Force-kill if graceful stop timed out
        force_kill_core_service(port=port, pid_file=pid_file, host=host, timeout=5.0)

    return start_core_daemon(
        host=host,
        port=port,
        log_file=log_file,
        log_level=log_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        pid_file=pid_file,
        timeout=timeout,
    )


def run_service_foreground(
    host: str = "0.0.0.0",
    port: int = 5000,
    log_file: Optional[Union[str, Path]] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    pid_file: Optional[Union[str, Path]] = None,
    console: bool = True,
) -> None:
    """Run Core Service synchronously in the foreground with headless configuration."""
    effective_log_file = Path(log_file) if log_file else _DEFAULT_LOG_FILE
    effective_pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE

    # 1. Setup rotating logging
    log = setup_rotating_logging(
        log_file=effective_log_file,
        log_level=log_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        console=console,
    )

    # 2. Record PID
    current_pid = os.getpid()
    write_pid_file(effective_pid_file, current_pid)

    def _cleanup():
        remove_pid_file(effective_pid_file)

    atexit.register(_cleanup)

    log.info(
        "Headless CoreDaemon started (PID: %d) listening on %s:%d. Log file: %s",
        current_pid,
        host,
        port,
        effective_log_file,
    )

    # 3. Create and configure server
    server = TcpJsonServer(host=host, port=port)

    # 4. Handle OS signals
    def _signal_handler(signum, frame):
        log.info("Received termination signal %s; stopping server gracefully...", signum)
        server.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass

    try:
        server.serve_forever()
    finally:
        _cleanup()
        log.info("CoreDaemon stopped cleanly.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless Core Daemon & Process Management Utility for B518 JetKVM Relay"
    )
    subparsers = parser.add_subparsers(dest="command", help="Management command")

    def _add_common_server_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
        p.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
        p.add_argument(
            "--log-file",
            default=str(_DEFAULT_LOG_FILE),
            help=f"Rotating log file path (default: {_DEFAULT_LOG_FILE})",
        )
        p.add_argument(
            "--log-level",
            default="INFO",
            help="Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)",
        )
        p.add_argument(
            "--max-bytes",
            type=int,
            default=10 * 1024 * 1024,
            help="Max log bytes before rotation (default: 10485760)",
        )
        p.add_argument(
            "--backup-count",
            type=int,
            default=5,
            help="Rotating backup count (default: 5)",
        )
        p.add_argument(
            "--pid-file",
            default=str(_DEFAULT_PID_FILE),
            help=f"PID file path (default: {_DEFAULT_PID_FILE})",
        )

    # run command (foreground)
    p_run = subparsers.add_parser("run", help="Run Core Service in foreground (blocking)")
    _add_common_server_args(p_run)

    # start command (background daemon)
    p_start = subparsers.add_parser("start", help="Start Core Service in background daemon mode")
    _add_common_server_args(p_start)
    p_start.add_argument("--timeout", type=float, default=10.0, help="Startup timeout in seconds")

    # stop command (graceful shutdown)
    p_stop = subparsers.add_parser("stop", help="Gracefully stop Core Service via TCP shutdown")
    p_stop.add_argument("--host", default="127.0.0.1", help="Target host interface")
    p_stop.add_argument("--port", type=int, default=5000, help="Target TCP port")
    p_stop.add_argument("--pid-file", default=str(_DEFAULT_PID_FILE), help="PID file path")
    p_stop.add_argument("--timeout", type=float, default=10.0, help="Stop timeout in seconds")

    # restart command
    p_restart = subparsers.add_parser("restart", help="Gracefully restart Core Service")
    _add_common_server_args(p_restart)
    p_restart.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")

    # status command
    p_status = subparsers.add_parser("status", help="Query Core Service status")
    p_status.add_argument("--host", default="127.0.0.1", help="Target host interface")
    p_status.add_argument("--port", type=int, default=5000, help="Target TCP port")
    p_status.add_argument("--pid-file", default=str(_DEFAULT_PID_FILE), help="PID file path")
    p_status.add_argument("--timeout", type=float, default=2.0, help="Query timeout in seconds")

    # force-kill command
    p_kill = subparsers.add_parser("force-kill", help="Aggressively force kill Core Service processes")
    p_kill.add_argument("--port", type=int, default=5000, help="Target TCP port")
    p_kill.add_argument("--pid-file", default=str(_DEFAULT_PID_FILE), help="PID file path")
    p_kill.add_argument("--host", default="127.0.0.1", help="Target host interface")
    p_kill.add_argument("--timeout", type=float, default=5.0, help="Recovery timeout in seconds")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cmd = args.command or "run"

    if cmd == "run":
        run_service_foreground(
            host=args.host,
            port=args.port,
            log_file=args.log_file,
            log_level=args.log_level,
            max_bytes=args.max_bytes,
            backup_count=args.backup_count,
            pid_file=args.pid_file,
        )
    elif cmd == "start":
        res = start_core_daemon(
            host=args.host,
            port=args.port,
            log_file=args.log_file,
            log_level=args.log_level,
            max_bytes=args.max_bytes,
            backup_count=args.backup_count,
            pid_file=args.pid_file,
            timeout=args.timeout,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "stop":
        res = graceful_stop_core_service(
            host=args.host,
            port=args.port,
            pid_file=args.pid_file,
            timeout=args.timeout,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "restart":
        res = restart_core_daemon(
            host=args.host,
            port=args.port,
            log_file=args.log_file,
            log_level=args.log_level,
            max_bytes=args.max_bytes,
            backup_count=args.backup_count,
            pid_file=args.pid_file,
            timeout=args.timeout,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "status":
        res = query_status(
            host=args.host,
            port=args.port,
            pid_file=args.pid_file,
            timeout=args.timeout,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "force-kill":
        res = force_kill_core_service(
            port=args.port,
            pid_file=args.pid_file,
            host=args.host,
            timeout=args.timeout,
        )
        print(json.dumps(res, indent=2))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
