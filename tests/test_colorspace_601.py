"""JetKVM frames are BT.601 data tagged BT.709: Core must decode them with the configured matrix.

Covers the decode function (ITU601 / auto / invalid setting), the reader on a correctly decoded screen, and the
annotated Log Solution reading pictures in monitor_start (success and result_cell_unknown_N failure).
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import av
import numpy as np
import pytest
from av.video.reformatter import Colorspace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import jetkvm_core  # noqa: E402
import monitor_flow  # noqa: E402
from fake_kvm import make_snapshot  # noqa: E402
from hid_session import FlowError  # noqa: E402
from jetkvm_core import (  # noqa: E402
    JetKVMClient, colorspace_name, decode_frame_bgr, resolve_decode_colorspace)
from log_solution_reader import (  # noqa: E402
    MarkerState, explain_app_frame, inspect_app_frame)
from match_diagnostics import MatchDiagnostics, wait_for_history  # noqa: E402
from protocol_helpers import LogSolutionKVM, render_log_solution  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

SWATCH = {"PASS": (0, 239, 0), "FAIL": (0, 0, 255), "TESTING": (0, 255, 255), "WAITING": (217, 217, 217)}


def yuv_frame(bgr, tag=Colorspace.ITU709):
    """What the JetKVM sends: BT.601 encoded yuv420p, but tagged ``tag`` (BT.709)."""
    frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(bgr), format="bgr24").reformat(
        format="yuv420p", dst_colorspace="ITU601")
    frame.colorspace = tag
    return frame


def swatch_frame():
    image = np.zeros((64, 16 * len(SWATCH), 3), np.uint8)
    for index, color in enumerate(SWATCH.values()):
        image[:, index * 16:(index + 1) * 16] = color
    return image


def distance(a, b):
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def test_601_decode_restores_contract_colours():
    decoded = decode_frame_bgr(yuv_frame(swatch_frame()), "ITU601")
    for index, (name, color) in enumerate(SWATCH.items()):
        # WAITING is grey, so the matrix does not matter for it; only 8-bit YUV rounding remains.
        assert distance(decoded[32, index * 16 + 8], color) <= (8 if name == "WAITING" else 5), name


def test_auto_follows_the_wrong_tag_and_reproduces_the_field_shift():
    decoded = decode_frame_bgr(yuv_frame(swatch_frame()), "auto")
    assert decoded[32, 8][1] < 210  # PASS green 239 reads ~199, the field symptom
    assert decoded[32, 8][1] > 190


def test_setting_values_and_invalid_falls_back_with_a_log(caplog, monkeypatch):
    monkeypatch.delenv(jetkvm_core.COLORSPACE_ENV, raising=False)
    assert resolve_decode_colorspace() == "ITU601"
    assert resolve_decode_colorspace("itu709") == "ITU709"
    assert resolve_decode_colorspace("AUTO") == "auto"
    monkeypatch.setenv(jetkvm_core.COLORSPACE_ENV, "ITU709")
    assert resolve_decode_colorspace() == "ITU709"
    with caplog.at_level(logging.WARNING, logger="JetKVMClient"):
        assert resolve_decode_colorspace("bt2020") == "ITU601"
    assert any("Invalid decode colorspace" in r.getMessage() for r in caplog.records)
    assert JetKVMClient("192.168.1.10", src_colorspace="nonsense").src_colorspace == "ITU601"


def test_colorspace_names():
    assert colorspace_name(1) == "ITU709"
    assert colorspace_name(2) == "UNSPECIFIED"
    assert colorspace_name(None) == "None"
    assert colorspace_name(1, ranges=True) == "MPEG"


def test_pump_video_logs_first_frame_and_snapshot_carries_colorspaces(caplog):
    class Track:
        def __init__(self):
            self.frames = [yuv_frame(swatch_frame()), yuv_frame(swatch_frame())]

        async def recv(self):
            if not self.frames:
                raise EOFError("done")
            return self.frames.pop(0)

    client = JetKVMClient("192.168.1.10")
    with caplog.at_level(logging.INFO, logger="JetKVMClient"):
        asyncio.run(client._pump_video(Track()))
    first = [r for r in caplog.records if "First video frame" in r.getMessage()]
    assert len(first) == 1  # once only
    assert "colorspace=ITU709" in first[0].getMessage() and "decoding with ITU601" in first[0].getMessage()
    assert first[0].zh
    snapshot = client.latest_frame()
    assert (snapshot.frame_colorspace, snapshot.decode_colorspace) == ("ITU709", "ITU601")
    assert distance(snapshot.image[32, 8], SWATCH["PASS"]) <= 5


def _ls_screen(statuses, state="complete"):
    return render_log_solution(state, statuses, scale=1, origin=(23, 31))


def test_reader_reads_pass_and_fail_after_601_decode():
    statuses = ["PASS", "FAIL", "PASS", "FAIL"]
    decoded = decode_frame_bgr(yuv_frame(_ls_screen(statuses)), "ITU601")
    now = time.monotonic()
    observation = inspect_app_frame(decoded, now, now=now)
    assert observation.reliable, observation.reason
    assert observation.statuses == tuple(statuses)
    assert observation.state == MarkerState.COMPLETE


def test_explain_shows_the_wrong_matrix_numbers():
    decoded = decode_frame_bgr(yuv_frame(_ls_screen(["PASS"] * 4)), "auto")
    explanation = explain_app_frame(decoded)
    assert explanation["located"] and not explanation["reliable"]
    assert explanation["reason"] == "result_cell_unknown_1"
    samples = explanation["failure"]["samples"]
    assert len(samples) == 4
    assert all(s["nearest"] == "PASS" and 30 <= s["distance"] <= 50 for s in samples)


def test_explain_survives_garbage():
    assert explain_app_frame(None)["located"] is False
    assert explain_app_frame(np.zeros((50, 50, 3), np.uint8))["located"] is False


class Gate:
    stats = None


def _template_root(tmp_path):
    root = tmp_path / "template"
    seed = LogSolutionKVM()
    x, y, w, h = seed.dock_icon_box()
    TemplateCatalog(root).save_crop(seed.latest_frame().image[y:y + h, x:x + w].copy(), (0, 0, w, h),
                                    "COMMON", "log_solution_dock_icon")
    return root


def test_monitor_start_success_saves_an_annotated_reading(tmp_path):
    root = _template_root(tmp_path)
    kvm = LogSolutionKVM(state="standby")
    kvm.close_window()
    kvm.restore_on_dock = True
    kvm.state_on_dock = "review"
    with pytest.raises(FlowError) as raised:
        asyncio.run(monitor_flow.run_monitor_start(
            kvm, Gate(), "DFU-01", template_root=root, confirm_sec=1.0, start_timeout_sec=0.1))
    assert raised.value.code == "log_solution_review"
    wait_for_history()
    latest = MatchDiagnostics.load(root, "DFU-01")
    record = [r for r in latest["records"] if r["key"] == "log_solution_reading"][0]
    assert record["matched"] is True
    assert (MatchDiagnostics.latest_directory(root, "DFU-01") / record["image"]).exists()
    assert "review" in record["note"]["en"]
    day = list((root / "_captures" / "match_diagnostics" / "DFU-01" / "history").iterdir())
    assert list(day[0].rglob("*log_solution_reading.jpg"))


class WrongMatrixKVM(LogSolutionKVM):
    """Window region decoded with the wrong matrix, as the field Core did; the Dock strip stays clean."""

    def latest_frame(self):
        snapshot = super().latest_frame()
        window_height = render_log_solution(None, []).shape[0]
        image = snapshot.image.copy()
        image[:window_height] = decode_frame_bgr(yuv_frame(image[:window_height]), "auto")
        return make_snapshot(image, sequence=snapshot.sequence, received_monotonic=snapshot.received_monotonic,
                             stream_id=snapshot.stream_id, pts=snapshot.pts)


def test_monitor_start_unknown_cell_failure_saves_sampled_values(tmp_path):
    root = _template_root(tmp_path)
    kvm = WrongMatrixKVM(state="complete", statuses=["PASS"] * 4)
    with pytest.raises(FlowError) as raised:
        asyncio.run(monitor_flow.run_monitor_start(
            kvm, Gate(), "DFU-01", template_root=root, confirm_sec=0.2, start_timeout_sec=0.1))
    assert raised.value.code == "log_solution_not_found"
    assert "result_cell_unknown_1" in str(raised.value)
    wait_for_history()
    directory = MatchDiagnostics.latest_directory(root, "DFU-01")
    record = [r for r in MatchDiagnostics.load(root, "DFU-01")["records"] if r["key"] == "confirm_last_unreliable"][0]
    assert (directory / record["image"]).exists()
    assert "corner samples BGR" in record["note"]["en"] and "PASS d=" in record["note"]["en"]
    assert "四角取樣" in record["note"]["zh"]
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    stored = [r for r in summary["records"] if r["key"] == "confirm_last_unreliable"][0]
    assert "BGR" in stored["note"]["en"]


def test_snapshot_tag_follows_each_frame_and_first_log_is_once(caplog):
    class Track:
        def __init__(self):
            self.frames = [yuv_frame(swatch_frame(), Colorspace.ITU709), yuv_frame(swatch_frame(), Colorspace.ITU601)]

        async def recv(self):
            if not self.frames:
                raise EOFError("done")
            return self.frames.pop(0)

    client = JetKVMClient("192.168.1.10", src_colorspace="auto")
    with caplog.at_level(logging.INFO, logger="JetKVMClient"):
        asyncio.run(client._pump_video(Track()))
    snapshot = client.latest_frame()
    assert (snapshot.frame_colorspace, snapshot.decode_colorspace) == ("ITU470BG", "auto")  # PyAV Colorspace.ITU601 is ffmpeg 5 (ITU470BG)
    assert distance(snapshot.image[32, 8], SWATCH["PASS"]) <= 5  # the last frame decoded with its own (601) tag
    assert len([r for r in caplog.records if "First video frame" in r.getMessage()]) == 1


def test_core_status_colorspace_follows_probe_and_round_entries():
    from fake_kvm import make_snapshot
    from reading_stats import ReadingStats, read_snapshot_recorded
    from round_frame_consumer import RoundFrameGate

    frame = render_log_solution("complete", ["PASS"] * 4, scale=1, origin=(23, 31))
    stats = ReadingStats()
    snapshot = make_snapshot(frame)
    snapshot = type(snapshot)(snapshot.image, snapshot.sequence, snapshot.received_monotonic, snapshot.stream_id,
                              snapshot.pts, "ITU709", "ITU601")
    read_snapshot_recorded(snapshot, stats)  # heartbeat probe entry
    assert (stats.report()["frame_colorspace"], stats.report()["decode_colorspace"]) == ("ITU709", "ITU601")
    read_snapshot_recorded(make_snapshot(frame), stats)  # a source without metadata reports null, not stale
    assert stats.report()["frame_colorspace"] is None and stats.report()["decode_colorspace"] is None

    class KVM:
        def latest_frame(self):
            return snapshot

        def is_healthy(self):
            return True

    from round_frame_consumer import observe_latest_round_frame
    stats = ReadingStats()
    observe_latest_round_frame(KVM(), RoundFrameGate("DFU-01", stats=stats))  # round entry
    assert (stats.report()["frame_colorspace"], stats.report()["decode_colorspace"]) == ("ITU709", "ITU601")
