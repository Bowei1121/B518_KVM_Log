"""monitor_start 1.0: bring Log Solution up from the Dock, then start monitoring with Cmd+Shift+M.

Fixed order, so no shortcut is sent unless Log Solution is on screen:
1. Monitoring or Review on screen: log_solution_busy, no HID at all.
2. Shared Dock icon template missing: hid_failed; Dock icon not on screen: log_solution_not_found.
3. Click the Dock icon; Log Solution must read reliably within the confirm window, else
   log_solution_not_found. Monitoring or Review after the click is busy. Still no shortcut.
4. Cmd+Shift+M; the device's round gate must read Monitoring within the start timeout,
   else monitor_not_started. The gate is armed by that reading, so the next check takes this round.
"""

import asyncio
import time

import cv2

from auto_flow import load_gray, locate
from match_diagnostics import MatchDiagnostics
from hid_helpers import center, last_hid_error
from round_frame_consumer import MarkerState, observe_latest_round_frame, read_latest_app_frame
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
MONITOR_CONFIRM_SEC = 3.0  # wait for Log Solution to show on screen after the Dock click
MONITOR_START_TIMEOUT_SEC = 10.0  # wait for Monitoring after Cmd+Shift+M
POLL_INTERVAL_SEC = 0.05


class MonitorStartError(Exception):
    """A refused or failed monitor_start. ``code`` is a protocol error code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _busy(state):
    return MonitorStartError("log_solution_busy", f"Log Solution 為 {state.value}，未送出快捷鍵")


async def _wait_for_reliable_reading(kvm, timeout_sec):
    """Newest reliable Log Solution reading within timeout_sec, or None."""
    deadline = time.monotonic() + timeout_sec
    while True:
        _frame, observation = read_latest_app_frame(kvm)
        if observation is not None and observation.reliable:
            return observation
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def click_dock_icon(kvm, frame, catalog, diagnostics, threshold=MATCH_THRESHOLD):
    """Click the shared Dock icon once. Returns ``(clicked, score)``; no click when the icon is not on screen.

    A missing template or a HID error raises ``MonitorStartError(hid_failed)``.
    """
    dock_path = catalog.path("COMMON", "log_solution_dock_icon")
    template = load_gray(str(dock_path))
    if template is None:
        raise MonitorStartError("hid_failed", f"缺少 Log Solution Dock 圖示模板: {dock_path}")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    value, left, top, width, height = locate(gray, template, downscale=False)
    box = (left, top, width, height) if left is not None else None
    diagnostics.record("log_solution_dock_icon", value, threshold, frame, box)
    if box is None or value < threshold:
        return False, value

    if hasattr(kvm, "rpc_errors"):
        kvm.rpc_errors.clear()
    await kvm.click(*center(left, top, width, height))
    if last_hid_error(kvm):
        raise MonitorStartError("hid_failed", f"Dock HID 錯誤: {last_hid_error(kvm)}")
    return True, value


async def bring_log_solution_forward(kvm, diagnostics_key, template_root=None, threshold=MATCH_THRESHOLD):
    """check's visibility recovery: one Dock click, no keys. Returns False when the Dock icon is not on screen.

    ``diagnostics_key`` is the device ID; match diagnostics are kept per device (ADR-0007).
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False
    try:
        frame, _observation = read_latest_app_frame(kvm)
        if frame is None:
            raise MonitorStartError("hid_failed", "沒有 JetKVM 影格")
        ok, _score = await click_dock_icon(kvm, frame, catalog, diagnostics, threshold)
        return ok
    finally:
        diagnostics.finalize(ok, "check_dock_recovery")


async def run_monitor_start(kvm, gate, diagnostics_key, template_root=None, threshold=MATCH_THRESHOLD,
                            confirm_sec=MONITOR_CONFIRM_SEC, start_timeout_sec=MONITOR_START_TIMEOUT_SEC,
                            log=print):
    """Start Log Solution monitoring. Returns ``{"state": "monitoring", "capacity": n}``.

    ``gate`` is the device's RoundFrameGate; the Monitoring reading arms it. ``diagnostics_key`` is the
    device ID; match diagnostics are kept per device (ADR-0007). Raises ``MonitorStartError`` with the
    protocol error code otherwise.
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False
    try:
        frame, observation = read_latest_app_frame(kvm)
        if frame is None:
            raise MonitorStartError("hid_failed", "沒有 JetKVM 影格")
        if observation.reliable and observation.state in (MarkerState.MONITORING, MarkerState.REVIEW):
            raise _busy(observation.state)

        clicked, score = await click_dock_icon(kvm, frame, catalog, diagnostics, threshold)
        if not clicked:
            raise MonitorStartError("log_solution_not_found",
                                    f"找不到 Log Solution Dock 圖示（相似度 {score:.2f}）")

        observation = await _wait_for_reliable_reading(kvm, confirm_sec)
        if observation is None:
            raise MonitorStartError("log_solution_not_found",
                                    f"點擊 Dock 後 {confirm_sec:g} 秒內找不到 Log Solution，未送出快捷鍵")
        if observation.state in (MarkerState.MONITORING, MarkerState.REVIEW):
            raise _busy(observation.state)

        await kvm.press_command_shift_m()
        if last_hid_error(kvm):
            raise MonitorStartError("hid_failed", f"快捷鍵 HID 錯誤: {last_hid_error(kvm)}")

        deadline = time.monotonic() + start_timeout_sec
        while True:
            decision = await asyncio.to_thread(observe_latest_round_frame, kvm, gate)
            if decision.state == MarkerState.MONITORING:
                ok = True
                return {"state": MarkerState.MONITORING.value, "capacity": int(decision.capacity)}
            if time.monotonic() >= deadline:
                raise MonitorStartError("monitor_not_started",
                                        f"送出 Cmd+Shift+M 後 {start_timeout_sec:g} 秒內未見監控中")
            await asyncio.sleep(POLL_INTERVAL_SEC)
    except MonitorStartError as exc:
        log(str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "monitor_start")
