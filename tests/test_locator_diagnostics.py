"""Locator pairing story in the diagnostics: candidates, pair checks, near-miss boxes, zoom picture, notes."""

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import monitor_flow  # noqa: E402
from hid_session import FlowError  # noqa: E402
from log_solution_reader import _locate_app, explain_app_frame  # noqa: E402
from match_diagnostics import MatchDiagnostics, draw_locator_zoom, wait_for_history  # noqa: E402
from protocol_helpers import LogSolutionKVM, render_log_solution  # noqa: E402
from test_colorspace_601 import Gate, WrongMatrixKVM, _template_root  # noqa: E402

ORIGIN = (23, 31)
BACKGROUND = (245, 246, 247)


def screen(statuses=("PASS", "FAIL", "PASS", "FAIL")):
    return render_log_solution("complete", list(statuses), scale=1, origin=ORIGIN)


def test_successful_pairing_reports_candidates_pair_and_origin():
    explanation = explain_app_frame(screen())
    locate = explanation["locate"]
    assert (len(locate["left"]), len(locate["right"]), len(locate["pairs"]), locate["valid_pairs"]) == (1, 1, 1, 1)
    assert locate["pairs"][0]["failed"] == []
    selected = locate["selected"]
    assert selected["origin"] == pytest.approx(ORIGIN, abs=1.0)
    assert selected["scale"] == pytest.approx(1.0, abs=0.05)
    assert "selected origin=" in locate["summary"]
    # the diagnostic scan and the real locator agree
    x, y, scale = _locate_app(screen())
    assert selected["origin"] == pytest.approx([x, y]) and selected["scale"] == pytest.approx(scale)
    samples = explanation["samples"]
    assert (len(samples["gutter"]), len(samples["marker"]), len(samples["heartbeat"])) == (3, 4, 4)


def test_masked_right_locator_leaves_only_left_candidates():
    frame = screen()
    x, y = ORIGIN[0] + 320, ORIGIN[1] + 2
    frame[y - 2:y + 24, x - 2:x + 24] = BACKGROUND
    explanation = explain_app_frame(frame)
    locate = explanation["locate"]
    assert len(locate["left"]) == 1 and len(locate["right"]) == 0
    assert locate["pairs"] == [] and locate["valid_pairs"] == 0 and locate["selected"] is None
    assert explanation["located"] is False
    assert "right 0" in locate["summary"] and "selected none" in locate["summary"]


def test_fake_square_with_misplaced_hole_is_listed_as_hole_offset():
    frame = screen()
    fx, fy = 60, 108
    frame[fy:fy + 22, fx:fx + 22] = (0, 0, 0)
    frame[fy + 8:fy + 16, fx + 8:fx + 16] = (255, 255, 255)  # hole midway between the left (3) and right (11) slots
    locate = explain_app_frame(frame)["locate"]
    fakes = [r for r in locate["rejected"] if abs(r["box"][0] - fx) <= 1 and abs(r["box"][1] - fy) <= 1]
    assert fakes and fakes[0]["reason"] == "hole_offset"
    assert len(locate["rejected"]) <= 20
    assert len(locate["left"]) == 1 and len(locate["right"]) == 1  # the real pair is unaffected


def test_zoom_picture_is_4x_and_missing_candidates_give_none():
    frame = screen()
    zoom = draw_locator_zoom(frame, explain_app_frame(frame))
    assert zoom is not None and zoom.shape[0] >= 4 * 40 and zoom.shape[1] > 1000
    blank = np.full((200, 300, 3), 245, np.uint8)
    assert draw_locator_zoom(blank, explain_app_frame(blank)) is None


def test_monitor_start_success_saves_locating_note_and_zoom(tmp_path):
    root = _template_root(tmp_path)
    kvm = LogSolutionKVM(state="standby")
    kvm.close_window()
    kvm.restore_on_dock = True
    kvm.state_on_dock = "review"
    with pytest.raises(FlowError):
        asyncio.run(monitor_flow.run_monitor_start(
            kvm, Gate(), "DFU-01", template_root=root, confirm_sec=1.0, start_timeout_sec=0.1))
    wait_for_history()
    records = MatchDiagnostics.load(root, "DFU-01")["records"]
    reading = [r for r in records if r["key"] == "log_solution_reading"][0]
    assert "selected origin=" in reading["note"]["en"] and "定位" in reading["note"]["zh"]
    zoom = [r for r in records if r["key"] == "log_solution_locators_zoom"][0]
    assert zoom["image"].endswith("_log_solution_locators_zoom.png")
    assert (MatchDiagnostics.latest_directory(root, "DFU-01") / zoom["image"]).exists()


def test_monitor_start_failure_saves_locating_note_and_zoom(tmp_path):
    root = _template_root(tmp_path)
    kvm = WrongMatrixKVM(state="complete", statuses=["PASS"] * 4)
    with pytest.raises(FlowError):
        asyncio.run(monitor_flow.run_monitor_start(
            kvm, Gate(), "DFU-01", template_root=root, confirm_sec=0.2, start_timeout_sec=0.1))
    wait_for_history()
    records = MatchDiagnostics.load(root, "DFU-01")["records"]
    failed = [r for r in records if r["key"] == "confirm_last_unreliable"][0]
    assert "left 1, right 1" in failed["note"]["en"] and "corner samples BGR" in failed["note"]["en"]
    assert any(r["key"] == "log_solution_locators_zoom" and r["image"] for r in records)
