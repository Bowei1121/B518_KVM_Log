"""Replay load tool: recording IO, the replay KVM's timeline, device mixing and a short real run."""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay_load as rl  # noqa: E402
import replay_load_test as tool  # noqa: E402
from protocol_helpers import render_log_solution  # noqa: E402


def _cycle_frames(fps=10):
    """standby 1 s, monitoring 2 s, complete 1 s, looped: a round the gate can take."""
    sequence = ([("standby", ["WAITING"] * 4)] * fps
                + [("monitoring", ["WAITING"] * 4)] * (2 * fps)
                + [("complete", ["PASS", "FAIL", "PASS", "PASS"])] * fps)
    frames = [render_log_solution(state, statuses) for state, statuses in sequence]
    pts = [round(i / fps + 100.0, 4) for i in range(len(frames))]
    return frames, pts


@pytest.fixture
def recording_dir(tmp_path):
    frames, pts = _cycle_frames()
    rl.write_recording(tmp_path / "rec", frames, pts)
    return tmp_path / "rec"


def test_recording_round_trips_frames_and_timestamps(recording_dir):
    recording = rl.load_recording(recording_dir)

    assert len(recording.frames) == 40
    assert recording.pts[0] == 100.0
    assert recording.frames[0].shape[2] == 3


def test_non_increasing_timestamps_are_refused(tmp_path):
    frames, _ = _cycle_frames()
    rl.write_recording(tmp_path / "bad", frames[:3], [1.0, 1.0, 2.0])

    with pytest.raises(ValueError, match="strictly increase"):
        rl.load_recording(tmp_path / "bad")


def test_missing_index_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        rl.load_recording(tmp_path)


def test_replay_kvm_plays_in_real_time_and_loops_with_advancing_timestamps(recording_dir):
    now = {"t": 1000.0}
    kvm = rl.ReplayKVM(rl.load_recording(recording_dir), name="x", clock=lambda: now["t"])

    first = kvm.latest_frame()
    now["t"] += 4.0 + 0.05  # one full loop later
    later = kvm.latest_frame()

    assert later[4] > first[4]
    assert later[1] > first[1]
    assert first[3] == "replay:x"
    assert all(0.0 <= age < 0.2 for age in kvm.frame_ages)


def test_replay_kvm_speed_scales_the_timeline(recording_dir):
    now = {"t": 0.0}
    slow = rl.ReplayKVM(rl.load_recording(recording_dir), clock=lambda: now["t"], speed=0.5)
    now["t"] = 1.0
    stamp = slow.latest_frame()[4]
    assert abs(stamp - 100.0 - 0.5) < 0.15  # half speed: one second of wall time is half a second of picture


def test_summary_fails_on_internal_errors_and_false_freezes():
    stats = rl.RunStats()
    stats.add_reply(0.1, "error", "internal_error")
    stats.add_reply(0.1, "error", "frame_frozen")
    limits = dict(tool.DEFAULT_LIMITS)

    summary = rl.summarise(stats, [0.01, 0.02], 10.0, limits)

    assert summary["verdict"] == "FAIL"
    assert summary["checks"]["no_internal_errors"] is False
    assert summary["checks"]["no_false_freeze_on_replay"] is False


def test_summary_passes_when_every_limit_holds():
    stats = rl.RunStats()
    stats.add_reply(0.2, "ok", "")
    stats.ping_latencies.append(0.01)
    stats.cpu_samples = [10.0, 12.0]
    stats.rss_samples_mb = [100.0, 101.0]

    summary = rl.summarise(stats, [0.01, 0.03], 10.0, dict(tool.DEFAULT_LIMITS))

    assert summary["verdict"] == "PASS"


def test_mixing_real_devices_requires_them_in_the_device_list(tmp_path):
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"BT-01": {"station": "BT", "kvm_ip": "192.168.132.78", "channels": 4}}),
                       encoding="utf-8")
    args = types.SimpleNamespace(include_real="BT-01,FCT-09", devices_file=str(devices), sim_count=0,
                                 sim_station="DFU", sim_start=61, channels=4)

    with pytest.raises(SystemExit, match="FCT-09"):
        tool.build_registry(args)


def test_simulated_ids_must_not_clash_with_real_ones(tmp_path):
    devices = tmp_path / "devices.json"
    devices.write_text(json.dumps({"DFU-61": {"station": "DFU", "kvm_ip": "192.168.132.90", "channels": 4}}),
                       encoding="utf-8")
    args = types.SimpleNamespace(include_real="DFU-61", devices_file=str(devices), sim_count=2,
                                 sim_station="DFU", sim_start=61, channels=4)

    with pytest.raises(SystemExit, match="clashes"):
        tool.build_registry(args)


def test_short_run_drives_simulated_devices_through_real_judgment(recording_dir, tmp_path):
    report_dir = tmp_path / "reports"
    code = tool.main([
        "--frames", str(recording_dir), "--sim-count", "2", "--duration", "12",
        "--check-timeout", "8", "--round-interval", "0.5", "--sample-interval", "1",
        "--report-dir", str(report_dir),
    ])

    reports = list(report_dir.glob("replay-*/report.json"))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    summary = data["summary"]
    assert data["config"]["simulated_devices"] == 2
    assert summary["replies"] > 0
    assert summary["reply_status_counts"].get("ok", 0) > 0
    assert summary["internal_errors"] == 0
    assert summary["frame_frozen_replies"] == 0
    assert summary["reply_kind_counts"].get("taken", 0) > 0  # simulated rounds were judged and taken
    assert (reports[0].parent / "report.md").exists()
    assert code in (0, 1)


def test_connection_errors_are_counted_apart_from_reply_latencies():
    stats = rl.RunStats()
    stats.add_reply(0.2, "ok", "")
    stats.add_connection_error()

    summary = rl.summarise(stats, [0.01], 10.0, dict(tool.DEFAULT_LIMITS))

    assert summary["connection_errors"] == 1
    assert summary["replies"] == 1
    assert summary["reply_latency_max_sec"] == 0.2
