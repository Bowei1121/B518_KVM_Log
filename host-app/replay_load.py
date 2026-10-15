"""Recorded-frame replay for load tests (ticket #11).

ReplayKVM stands in for a JetKVM: it serves a recorded picture sequence with its own
presentation timestamps, in real time, through the same latest_frame() seam the real
client uses. Simulated devices therefore go through Core's judgment and long-poll path
unchanged. The helpers here load and write recordings and summarise a load run.
"""

import json
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

RECORDING_INDEX = "frames.jsonl"


@dataclass
class Recording:
    frames: List[np.ndarray]
    pts: List[float]  # presentation timestamps, seconds, increasing within one loop

    @property
    def loop_length(self) -> float:
        return self.pts[-1] - self.pts[0] + self._frame_period()

    def _frame_period(self) -> float:
        if len(self.pts) < 2:
            return 1.0 / 30
        return (self.pts[-1] - self.pts[0]) / (len(self.pts) - 1)


def load_recording(directory: Path) -> Recording:
    """Read ``frames.jsonl`` (one {"file", "pts"} object per line) and the PNG frames it names."""
    directory = Path(directory)
    index = directory / RECORDING_INDEX
    if not index.is_file():
        raise FileNotFoundError(f"recording index not found: {index}")
    frames, pts = [], []
    for line_no, line in enumerate(index.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        entry = json.loads(line)
        image = cv2.imread(str(directory / entry["file"]), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"{index}:{line_no}: cannot read frame {entry['file']}")
        frames.append(image)
        pts.append(float(entry["pts"]))
    if not frames:
        raise ValueError(f"recording has no frames: {index}")
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError(f"{index}: presentation timestamps must strictly increase")
    return Recording(frames=frames, pts=pts)


def write_recording(directory: Path, frames: Sequence[np.ndarray], pts: Sequence[float]) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, (frame, stamp) in enumerate(zip(frames, pts)):
        name = f"f{index:05d}.png"
        cv2.imwrite(str(directory / name), frame)
        lines.append(json.dumps({"file": name, "pts": stamp}))
    (directory / RECORDING_INDEX).write_text("\n".join(lines) + "\n", encoding="utf-8")


class ReplayKVM:
    """Serves ``recording`` in real time, looping. Records how old each served frame was."""

    def __init__(self, recording: Recording, name: str = "replay", speed: float = 1.0,
                 phase: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.recording = recording
        self.name = name
        self.speed = float(speed)
        self.phase = float(phase)
        self.clock = clock
        self.started = clock()
        self.connected = False
        self.frame_ages: List[float] = []
        self._sequence = 0
        self._lock = threading.Lock()

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    def latest_frame(self) -> Tuple[np.ndarray, int, float, str, float]:
        """(frame, sequence, received_at, stream_id, pts) for the picture on screen now."""
        now = self.clock()
        with self._lock:
            media_time = (now - self.started) * self.speed + self.phase
            period = self.recording._frame_period()
            count = len(self.recording.frames)
            slot = int(media_time / period)
            index = slot % count
            loop = slot // count
            pts = self.recording.pts[index] + loop * self.recording.loop_length
            emitted_at = self.started + (slot * period - self.phase) / self.speed
            self._sequence += 1
            self.frame_ages.append(max(0.0, now - emitted_at))
            return (self.recording.frames[index], self._sequence, emitted_at,
                    f"replay:{self.name}", pts)


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[rank]


@dataclass
class RunStats:
    """Collected during a run and summarised by summarise()."""

    reply_latencies: List[float] = field(default_factory=list)
    reply_statuses: dict = field(default_factory=dict)
    kind_counts: dict = field(default_factory=dict)
    ping_latencies: List[float] = field(default_factory=list)
    cpu_samples: List[float] = field(default_factory=list)
    rss_samples_mb: List[float] = field(default_factory=list)
    internal_errors: int = 0
    frozen_replies: int = 0
    connection_errors: int = 0  # no reply came back, so these are not in reply_latencies
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_reply(self, latency: float, status: str, error: str, kind: str = "") -> None:
        with self.lock:
            self.reply_latencies.append(latency)
            key = status if not error else f"{status}:{error}"
            self.reply_statuses[key] = self.reply_statuses.get(key, 0) + 1
            if kind:
                self.kind_counts[kind] = self.kind_counts.get(kind, 0) + 1
            if error == "internal_error":
                self.internal_errors += 1
            if error == "frame_frozen":
                self.frozen_replies += 1

    def add_connection_error(self) -> None:
        with self.lock:
            self.connection_errors += 1


def summarise(stats: RunStats, frame_ages: Sequence[float], duration: float,
              limits: dict) -> dict:
    """Numbers for the report plus PASS/FAIL against the limits."""
    cpu = stats.cpu_samples
    rss = stats.rss_samples_mb
    summary = {
        "duration_sec": round(duration, 1),
        "replies": len(stats.reply_latencies),
        "reply_status_counts": dict(sorted(stats.reply_statuses.items())),
        "reply_kind_counts": dict(sorted(stats.kind_counts.items())),
        "reply_latency_p50_sec": _round(percentile(stats.reply_latencies, 0.50)),
        "reply_latency_p95_sec": _round(percentile(stats.reply_latencies, 0.95)),
        "reply_latency_max_sec": _round(max(stats.reply_latencies) if stats.reply_latencies else None),
        "ping_latency_p95_sec": _round(percentile(stats.ping_latencies, 0.95)),
        "frame_age_samples": len(frame_ages),
        "frame_age_p50_sec": _round(percentile(frame_ages, 0.50)),
        "frame_age_p95_sec": _round(percentile(frame_ages, 0.95)),
        "frame_age_max_sec": _round(max(frame_ages) if frame_ages else None),
        "cpu_avg_percent": _round(statistics.fmean(cpu) if cpu else None, 1),
        "cpu_max_percent": _round(max(cpu) if cpu else None, 1),
        "rss_start_mb": _round(rss[0] if rss else None, 1),
        "rss_max_mb": _round(max(rss) if rss else None, 1),
        "rss_end_mb": _round(rss[-1] if rss else None, 1),
        "internal_errors": stats.internal_errors,
        "frame_frozen_replies": stats.frozen_replies,
        "connection_errors": stats.connection_errors,
    }
    checks = {
        "no_internal_errors": stats.internal_errors == 0,
        "no_false_freeze_on_replay": stats.frozen_replies == 0,
        "frame_age_p95_within_limit": _within(summary["frame_age_p95_sec"], limits["frame_age_p95_sec"]),
        "ping_p95_within_limit": _within(summary["ping_latency_p95_sec"], limits["ping_p95_sec"]),
        "cpu_avg_within_limit": _within(summary["cpu_avg_percent"], limits["cpu_avg_percent"]),
        "rss_growth_within_limit": (
            rss is None or len(rss) < 2 or (rss[-1] - rss[0]) <= limits["rss_growth_mb"]
        ),
    }
    summary["checks"] = checks
    summary["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    return summary


def _within(value: Optional[float], limit: float) -> bool:
    return value is not None and value <= limit


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(value, digits)


def markdown_report(summary: dict, config: dict) -> str:
    lines = [f"# 錄影重播壓測報告：{summary['verdict']}", "", "## 設定", ""]
    lines += [f"- {key}: {value}" for key, value in config.items()]
    lines += ["", "## 結果", ""]
    for key, value in summary.items():
        if key in ("checks", "verdict"):
            continue
        lines.append(f"- {key}: {value}")
    lines += ["", "## 判定", ""]
    lines += [f"- {'通過' if ok else '未通過'} {name}" for name, ok in summary["checks"].items()]
    return "\n".join(lines) + "\n"
