"""core_status reading fields (issue #94): frame size, picture scale, cell pixels, last reading, readable ratio.

Every recorded reading counts once. core_status only reports; it never reads a frame.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from fake_kvm import FakeKVM, make_snapshot  # noqa: E402
from log_solution_reader import ActivityTracker, FrameObservation, MarkerState  # noqa: E402
from reading_stats import READING_WINDOW, ReadingStats  # noqa: E402
from round_frame_consumer import RoundFrameGate  # noqa: E402
from tcp_server import TcpJsonServer, _inspect_round  # noqa: E402
from ui_app import format_reading_metrics  # noqa: E402

from protocol_helpers import make_registry, render_log_solution  # noqa: E402

READABLE = FrameObservation(True, MarkerState.MONITORING, 2, ("TESTING", "PASS"), scale=2.0)
UNREADABLE = FrameObservation(False, reason="locators_missing_or_ambiguous")


def test_nothing_recorded_reports_nulls_and_zero_samples():
    report = ReadingStats().report(now=100.0)

    assert report == {
        "frame_size": None,
        "picture_scale": None,
        "cell_px": None,
        "last_reading": None,
        "readable_ratio": {"ratio": None, "samples": 0},
    }


def test_unlocated_reading_has_no_picture_scale_but_gives_its_reason():
    stats = ReadingStats()
    stats.record(UNREADABLE, None, now=5.0)

    report = stats.report(now=6.0)

    assert report["picture_scale"] is None
    assert report["cell_px"] is None
    assert report["frame_size"] is None
    assert report["last_reading"] == {"reason": "locators_missing_or_ambiguous", "age_sec": 1.0}


def test_readable_ratio_covers_only_the_last_fifty_readings():
    stats = ReadingStats()
    for _ in range(READING_WINDOW + 10):
        stats.record(READABLE, None, now=1.0)
    for _ in range(10):
        stats.record(UNREADABLE, None, now=2.0)

    ratio = stats.report(now=2.0)["readable_ratio"]

    assert ratio["samples"] == READING_WINDOW
    assert ratio["ratio"] == pytest.approx(0.8)


def test_gate_records_one_reading_per_inspected_frame():
    stats = ReadingStats()
    gate = RoundFrameGate("DFU-01", require_presentation_time=False, stats=stats)
    frame = render_log_solution("monitoring", ["TESTING", "PASS"], scale=2)

    gate.observe(frame, 1, 10.0, now=10.0, stream_id="s")

    assert stats.report(now=10.0)["readable_ratio"]["samples"] == 1


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_decoded_frame_gives_size_scale_and_cell_pixels_at_one_and_two_times(scale):
    stats = ReadingStats()
    frame = render_log_solution("monitoring", ["TESTING", "PASS"], scale=scale)

    _inspect_round(FakeKVM(frame), ActivityTracker(), None, None, stats)

    report = stats.report()
    assert report["frame_size"] == [frame.shape[1], frame.shape[0]]
    assert report["picture_scale"] == pytest.approx(scale, abs=0.05)
    assert report["cell_px"] == pytest.approx(scale * 10, abs=0.5)
    assert report["readable_ratio"]["samples"] == 1


def test_disconnected_device_reports_no_frame_size_but_keeps_history():
    import numpy as np

    server = TcpJsonServer(device_registry=make_registry())
    server._slot("DFU-01").stats.record(READABLE, np.zeros((220, 752, 3), dtype=np.uint8), now=0.0)

    row = next(row for row in server._station_rows() if row["device"] == "DFU-01")

    assert row["is_connected"] is False
    assert row["frame_size"] is None
    assert row["readable_ratio"]["samples"] == 1


def test_inspect_round_records_its_one_read():
    stats = ReadingStats()
    kvm = FakeKVM(render_log_solution("monitoring", ["TESTING", "PASS"], scale=2))

    _inspect_round(kvm, ActivityTracker(), None, None, stats)

    report = stats.report()
    assert report["readable_ratio"]["samples"] == 1
    assert report["picture_scale"] == pytest.approx(2.0, abs=0.001)


def test_station_rows_report_slot_stats_and_nulls_for_devices_without_a_slot():
    server = TcpJsonServer(device_registry=make_registry())
    server._slot("DFU-01").stats.record(READABLE, None, now=0.0)

    rows = {row["device"]: row for row in server._station_rows()}

    assert rows["DFU-01"]["readable_ratio"]["samples"] == 1
    assert rows["DFU-02"]["frame_size"] is None
    assert rows["DFU-02"]["readable_ratio"] == {"ratio": None, "samples": 0}
    assert rows["DFU-02"]["last_reading"] is None


def test_format_reading_metrics_shows_size_scale_cell_and_ratio():
    text = format_reading_metrics({
        "frame_size": [752, 220], "picture_scale": 2.0, "cell_px": 20.0,
        "last_reading": {"reason": "", "age_sec": 0.4},
        "readable_ratio": {"ratio": 0.9, "samples": 50},
    })

    assert "影格 752×220" in text
    assert "換算 2.000" in text
    assert "小格 20.0 px" in text
    assert "可讀率 90% (最近 50 次)" in text


def test_format_reading_metrics_without_readings_shows_dashes():
    text = format_reading_metrics({
        "frame_size": None, "picture_scale": None, "cell_px": None,
        "last_reading": None, "readable_ratio": {"ratio": None, "samples": 0},
    })

    assert "影格 —" in text
    assert "可讀率 — (0 次)" in text
