#!/usr/bin/env python3
"""TE Operator Management Utility for B518 JetKVM Relay Core Service.

Usage:
    python tools/manage_core.py start [--port 5000] [--host 0.0.0.0]
    python tools/manage_core.py stop [--port 5000]
    python tools/manage_core.py restart [--port 5000]
    python tools/manage_core.py status [--port 5000]
    python tools/manage_core.py force-kill [--port 5000]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure host-app is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_APP_DIR = _REPO_ROOT / "host-app"
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from core_daemon import (
    force_kill_core_service,
    graceful_stop_core_service,
    query_status,
    restart_core_daemon,
    start_core_daemon,
)


def print_banner(title: str) -> None:
    print("=" * 64)
    print(f" {title}")
    print("=" * 64)


def handle_start(args: argparse.Namespace) -> int:
    print(f"Starting Core Service on {args.host}:{args.port}...")
    try:
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
        if res.get("status") == "already_running":
            print(f"[WARN] {res.get('message')}")
            return 0
        print(f"[OK] {res.get('message')}")
        print(f"     PID:      {res.get('pid')}")
        print(f"     Port:     {res.get('port')}")
        print(f"     Log file: {res.get('log_file')}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Failed to start Core Service: {exc}", file=sys.stderr)
        return 1


def handle_stop(args: argparse.Namespace) -> int:
    print(f"Sending graceful shutdown request to {args.host}:{args.port}...")
    res = graceful_stop_core_service(
        host=args.host,
        port=args.port,
        pid_file=args.pid_file,
        timeout=args.timeout,
    )
    if res.get("status") == "ok":
        print(f"[OK] {res.get('message')}")
        return 0
    else:
        print(f"[WARN] {res.get('message')}")
        print("Tip: If the process is frozen, run 'force-kill' to aggressively recover.")
        return 1


def handle_restart(args: argparse.Namespace) -> int:
    print(f"Restarting Core Service on {args.host}:{args.port}...")
    try:
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
        print(f"[OK] {res.get('message')}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Failed to restart Core Service: {exc}", file=sys.stderr)
        return 1


def handle_status(args: argparse.Namespace) -> int:
    res = query_status(
        host=args.host,
        port=args.port,
        pid_file=args.pid_file,
        timeout=args.timeout,
    )
    st = res.get("status", "unknown").upper()
    print_banner(f"B518 JetKVM Relay Core Service — Status: {st}")
    print(f"  Target:            {args.host}:{args.port}")
    print(f"  PID:               {res.get('pid') or 'N/A'}")

    if st == "RUNNING":
        details = res.get("details", {})
        uptime = details.get("uptime_seconds", "N/A")
        active_conns = details.get("active_connections", "N/A")
        busy_devices = details.get("busy_devices", [])
        kvm_pool = details.get("kvm_pool", {})
        freeze_guard = details.get("freeze_guard", {})

        print(f"  Uptime:            {uptime}s")
        print(f"  TCP Clients:       {active_conns}")
        print(f"  Busy Devices:      {busy_devices}")
        print(f"  KVM Pool:          {kvm_pool.get('active_connections', 0)} active ({kvm_pool.get('cached_ips', [])})")
        print(f"  Freeze Guard:      violations={freeze_guard.get('violations', 0)}")
        print(f"  Server Version:    {details.get('version', 'unknown')}")
        print("=" * 64)
        return 0
    elif st == "UNRESPONSIVE":
        print(f"  Warning:           {res.get('message')}")
        print("  Action:            Consider running 'manage_core.py force-kill'")
        print("=" * 64)
        return 2
    else:
        print(f"  State:             Core service is currently stopped.")
        print("=" * 64)
        return 0


def handle_force_kill(args: argparse.Namespace) -> int:
    print_banner(f"Emergency Recovery: Force Kill Core Service on port {args.port}")
    res = force_kill_core_service(
        port=args.port,
        pid_file=args.pid_file,
        host=args.host,
        timeout=args.timeout,
    )
    killed = res.get("killed_pids", [])
    freed = res.get("port_released", False)

    print(f"  Killed PIDs:       {killed if killed else 'None found'}")
    print(f"  Port {args.port} status:   {'RELEASED (FREE)' if freed else 'STILL BUSY'}")
    print(f"  Result:            {res.get('message')}")
    print("=" * 64)
    return 0 if freed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TE Management Utility for B518 JetKVM Relay Core Service"
    )
    subparsers = parser.add_subparsers(dest="action", help="Management action")

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
        p.add_argument("--port", type=int, default=5000, help="TCP port (default: 5000)")
        p.add_argument("--pid-file", default=None, help="PID file path (optional)")
        p.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")

    # start
    p_start = subparsers.add_parser("start", help="Start Core Service daemon")
    _add_common(p_start)
    p_start.add_argument("--log-file", default=None, help="Path to rotating log file")
    p_start.add_argument("--log-level", default="INFO", help="Logging level")
    p_start.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024, help="Max log bytes")
    p_start.add_argument("--backup-count", type=int, default=5, help="Backup count")

    # stop
    p_stop = subparsers.add_parser("stop", help="Gracefully stop Core Service")
    _add_common(p_stop)

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart Core Service")
    _add_common(p_restart)
    p_restart.add_argument("--log-file", default=None, help="Path to rotating log file")
    p_restart.add_argument("--log-level", default="INFO", help="Logging level")
    p_restart.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024, help="Max log bytes")
    p_restart.add_argument("--backup-count", type=int, default=5, help="Backup count")

    # status
    p_status = subparsers.add_parser("status", help="Check status of Core Service")
    p_status.add_argument("--host", default="127.0.0.1", help="Target host")
    p_status.add_argument("--port", type=int, default=5000, help="Target TCP port")
    p_status.add_argument("--pid-file", default=None, help="PID file path")
    p_status.add_argument("--timeout", type=float, default=2.0, help="Timeout in seconds")

    # force-kill
    p_kill = subparsers.add_parser("force-kill", help="Aggressively kill lingering processes and free port")
    p_kill.add_argument("--host", default="127.0.0.1", help="Target host")
    p_kill.add_argument("--port", type=int, default=5000, help="Target TCP port")
    p_kill.add_argument("--pid-file", default=None, help="PID file path")
    p_kill.add_argument("--timeout", type=float, default=5.0, help="Timeout in seconds")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "start": handle_start,
        "stop": handle_stop,
        "restart": handle_restart,
        "status": handle_status,
        "force-kill": handle_force_kill,
    }

    exit_code = dispatch[args.action](args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
