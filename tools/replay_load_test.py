#!/usr/bin/env python3
"""Replay-driven load test for Core Service (ticket #11).

Simulated devices are backed by a recorded JetKVM picture sequence (ReplayKVM). They are
ordinary devices in the device list, so Core judges and long-polls them exactly like real
ones. Real devices can be mixed in from a device list. One long-polling client runs per
device, and a pinger measures how quickly Core answers while the load runs.

Example (6 simulated devices plus 3 real ones, 30 minutes):
    python tools/replay_load_test.py --frames recordings/log_solution_30fps \\
        --sim-count 6 --include-real DFU-01,FCT-01,BT-01 --duration 1800 --report-dir reports
"""

import argparse
import json
import os
import resource
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host-app"))

from device_registry import DeviceEntry, DeviceRegistry, default_registry_path  # noqa: E402
from freeze_guard import FrameFreezeGuard  # noqa: E402
from kvm_pool import KVMConnectionPool, _default_client_factory  # noqa: E402
from replay_load import (  # noqa: E402
    ReplayKVM, RunStats, load_recording, markdown_report, summarise,
)
from tcp_server import TcpJsonServer  # noqa: E402

SIM_IP_PREFIX = "10.255"
DEFAULT_LIMITS = {
    "frame_age_p95_sec": 0.5,   # pictures read by Core are at most this old at the 95th percentile
    "ping_p95_sec": 0.2,        # Core answers ping within this at the 95th percentile under load
    "cpu_avg_percent": 70.0,    # average Core process CPU
    "rss_growth_mb": 100.0,     # memory growth over the whole run
}


def build_registry(args):
    """Real devices (optional) plus simulated devices. Returns (registry, sim_ips, real_ids)."""
    entries, real_ids = [], []
    if args.include_real:
        wanted = {item.strip() for item in args.include_real.split(",") if item.strip()}
        real = DeviceRegistry.load(args.devices_file).entries()
        found = {e.device_id for e in real}
        missing = wanted - found
        if missing:
            raise SystemExit(f"devices not in the device list: {', '.join(sorted(missing))}")
        entries += [e for e in real if e.device_id in wanted]
        real_ids = [e.device_id for e in entries]
    real_keys = {e.device_id for e in entries}
    sim_ips = {}
    for index in range(args.sim_count):
        device_id = f"{args.sim_station}-{args.sim_start + index:02d}"
        if device_id in real_keys:
            raise SystemExit(f"simulated device {device_id} clashes with a real device")
        ip = f"{SIM_IP_PREFIX}.{index // 250}.{index % 250 + 1}"
        entries.append(DeviceEntry(device_id=device_id, station=args.sim_station, kvm_ip=ip,
                                   channels=args.channels))
        sim_ips[ip] = device_id
    if not entries:
        raise SystemExit("nothing to test: give --sim-count and/or --include-real")
    return DeviceRegistry(entries), sim_ips, real_ids


class LoadClient(threading.Thread):
    """One device's LabVIEW-like long poll: check, read the reply, pause between rounds."""

    def __init__(self, host, port, device, args, stats, stop):
        super().__init__(daemon=True, name=f"load-{device}")
        self.host, self.port, self.device = host, port, device
        self.args, self.stats, self.stop_event = args, stats, stop
        self.counter = 0

    def run(self):
        sock = None
        while not self.stop_event.is_set():
            try:
                if sock is None:
                    sock = socket.create_connection((self.host, self.port), timeout=5.0)
                self.counter += 1
                req_id = f"{self.device}-{self.counter}"
                payload = {"protocol_version": "1.0", "req_id": req_id, "device": self.device,
                           "cmd": "check", "timeout_sec": self.args.check_timeout}
                started = time.monotonic()
                sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                sock.settimeout(self.args.check_timeout + 15.0)
                reply = read_line(sock)
                latency = time.monotonic() - started
                kind = (reply.get("data") or {}).get("kind", "")
                self.stats.add_reply(latency, reply.get("status", "?"), reply.get("error", ""), kind)
                if kind in ("taken", "already_taken") or reply.get("status") != "ok":
                    self.stop_event.wait(self.args.round_interval)
            except (OSError, ValueError, ConnectionError):
                self.stats.add_reply(0.0, "client", "connection")
                if sock is not None:
                    sock.close()
                sock = None
                self.stop_event.wait(1.0)
        if sock is not None:
            sock.close()


def read_line(sock):
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("closed by Core")
        buf.extend(chunk)
    return json.loads(bytes(buf).partition(b"\n")[0].decode("utf-8"))


class Pinger(threading.Thread):
    def __init__(self, host, port, stats, stop, interval=1.0):
        super().__init__(daemon=True, name="pinger")
        self.host, self.port, self.stats, self.stop_event, self.interval = host, port, stats, stop, interval

    def run(self):
        while not self.stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
                    sock.settimeout(5.0)
                    started = time.monotonic()
                    sock.sendall(b'{"protocol_version": "1.0", "req_id": "ping", "device": "", "cmd": "ping"}\n')
                    read_line(sock)
                    self.stats.ping_latencies.append(time.monotonic() - started)
            except (OSError, ValueError, ConnectionError):
                pass
            self.stop_event.wait(self.interval)


def rss_mb():
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        if out:
            return int(out) / 1024.0
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0


class Sampler(threading.Thread):
    def __init__(self, stats, stop, interval):
        super().__init__(daemon=True, name="sampler")
        self.stats, self.stop_event, self.interval = stats, stop, interval

    def run(self):
        last_cpu, last_wall = time.process_time(), time.monotonic()
        while not self.stop_event.wait(self.interval):
            cpu, wall = time.process_time(), time.monotonic()
            self.stats.cpu_samples.append(100.0 * (cpu - last_cpu) / max(1e-9, wall - last_wall))
            self.stats.rss_samples_mb.append(rss_mb())
            last_cpu, last_wall = cpu, wall


def run(args):
    recording = load_recording(Path(args.frames))
    registry, sim_ips, real_ids = build_registry(args)
    period = recording._frame_period()
    sims = {}
    for index, ip in enumerate(sim_ips):
        device = sim_ips[ip]
        sims[ip] = ReplayKVM(recording, name=device, speed=args.speed,
                             phase=(index * 0.37) % max(period, 1e-3))

    def factory(host, password=""):
        if host in sims:
            return sims[host]
        return _default_client_factory(host, password=password)

    server = TcpJsonServer(
        device_registry=registry, host="127.0.0.1", port=0,
        kvm_pool=KVMConnectionPool(client_factory=factory),
        freeze_guard=FrameFreezeGuard(freeze_threshold=args.freeze_threshold),
        template_root=args.template_root,
    )
    server.start()
    host, port = server.server_address
    stats = RunStats()
    stop = threading.Event()
    clients = [LoadClient(host, port, e.device_id, args, stats, stop) for e in registry.entries()]
    workers = clients + [Pinger(host, port, stats, stop), Sampler(stats, stop, args.sample_interval)]
    started = time.monotonic()
    for worker in workers:
        worker.start()
    try:
        time.sleep(args.duration)
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=30)
        server.stop()
        server.wait(timeout=5)
    duration = time.monotonic() - started
    ages = [age for kvm in sims.values() for age in kvm.frame_ages]
    config = {
        "frames": str(args.frames), "frame_count": len(recording.frames),
        "source_period_sec": round(period, 4), "speed": args.speed,
        "simulated_devices": len(sims), "real_devices": ",".join(real_ids) or "none",
        "check_timeout_sec": args.check_timeout, "round_interval_sec": args.round_interval,
    }
    summary = summarise(stats, ages, duration, DEFAULT_LIMITS)
    return config, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frames", required=True, help="recording directory (frames.jsonl + PNG frames)")
    parser.add_argument("--sim-count", type=int, default=6, help="simulated devices (default 6)")
    parser.add_argument("--sim-station", default="DFU", choices=("DFU", "FCT", "BT"))
    parser.add_argument("--sim-start", type=int, default=61, help="first simulated number, 61 keeps clear of real IDs")
    parser.add_argument("--channels", type=int, default=4)
    parser.add_argument("--include-real", default="", help="comma-separated real device IDs from the device list")
    parser.add_argument("--devices-file", default=str(default_registry_path()))
    parser.add_argument("--template-root", default=None)
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed of the recording")
    parser.add_argument("--duration", type=float, default=1800.0, help="seconds (default 1800 = 30 min)")
    parser.add_argument("--check-timeout", type=float, default=600.0, help="check timeout_sec per round")
    parser.add_argument("--round-interval", type=float, default=5.0, help="pause after a finished round")
    parser.add_argument("--freeze-threshold", type=float, default=1.0)
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args(argv)

    config, summary = run(args)
    out = Path(args.report_dir) / datetime.now().strftime("replay-%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"config": config, "summary": summary}, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    (out / "report.md").write_text(markdown_report(summary, config), encoding="utf-8")
    print(f"verdict: {summary['verdict']}  report: {out}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
