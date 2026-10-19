"""monitor_start 1.0: bring Log Solution up from the Dock, then start monitoring with Cmd+Shift+M.

Fixed order, so no shortcut is sent unless Log Solution is on screen:
1. Monitoring on screen: log_solution_busy, no HID. Review: log_solution_review, no HID.
2. Preparing: do not click the Dock and do not send the shortcut; wait for Monitoring.
3. Otherwise click the shared Dock icon. Template missing: hid_failed. Icon absent or the
   window never appears: log_solution_not_found.
4. After the click, Monitoring is busy and Review is log_solution_review. Preparing waits
   without a shortcut. Standby, Complete and ManualStopComplete send Cmd+Shift+M.
5. Until timeout_sec: Preparing keeps waiting, Review returns log_solution_review at once,
   Monitoring succeeds. Otherwise monitor_not_started. Seeing Preparing or sending the
   shortcut establishes the round-start request; monitor_not_started does not clear it.
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


def neutral_pointer_position(frame):
    """Where the pointer rests after a Dock click: top edge, mid-screen.

    Off the Dock (no hover magnification over the icon), off the Log Solution band, and away from
    the screen corners (macOS Hot Corners).
    """
    return frame.shape[1] // 2, 1


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
    if hasattr(kvm, "move"):
        await kvm.move(*neutral_pointer_position(frame))
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


def _review(where):
    return MonitorStartError("log_solution_review", f"Log Solution 為待確認（{where}），未繼續開始監控")


async def run_monitor_start(kvm, gate, diagnostics_key, template_root=None, threshold=MATCH_THRESHOLD,
                            confirm_sec=MONITOR_CONFIRM_SEC, start_timeout_sec=MONITOR_START_TIMEOUT_SEC,
                            log=print, on_preparing=None, on_shortcut=None, on_monitoring=None):
    """Start Log Solution monitoring. Returns ``{"state": "monitoring", "capacity": n}``.

    ``gate`` is the device's RoundFrameGate; the Monitoring reading arms it. ``diagnostics_key`` is the
    device ID; match diagnostics are kept per device (ADR-0007). Raises ``MonitorStartError`` with the
    protocol error code otherwise. Callbacks record the round-start request; a timeout does not undo them.
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False

    def preparing():
        if on_preparing is not None:
            on_preparing()

    try:
        frame, observation = read_latest_app_frame(kvm)
        if frame is None:
            raise MonitorStartError("hid_failed", "沒有 JetKVM 影格")
        if observation.reliable and observation.state == MarkerState.MONITORING:
            if on_monitoring is not None:
                on_monitoring()
            raise _busy(observation.state)
        if observation.reliable and observation.state == MarkerState.REVIEW:
            raise _review("開始前")
        if not (observation.reliable and observation.state == MarkerState.PREPARING):
            clicked, score = await click_dock_icon(kvm, frame, catalog, diagnostics, threshold)
            if not clicked:
                raise MonitorStartError("log_solution_not_found",
                                        f"找不到 Log Solution Dock 圖示（相似度 {score:.2f}）")
            observation = await _wait_for_reliable_reading(kvm, confirm_sec)
            if observation is None:
                raise MonitorStartError("log_solution_not_found",
                                        f"點擊 Dock 後 {confirm_sec:g} 秒內找不到 Log Solution，未送出快捷鍵")
            if observation.state == MarkerState.MONITORING:
                if on_monitoring is not None:
                    on_monitoring()
                raise _busy(observation.state)
            if observation.state == MarkerState.REVIEW:
                raise _review("點擊 Dock 後")
            if observation.state != MarkerState.PREPARING:
                await kvm.press_command_shift_m()
                if on_shortcut is not None:
                    on_shortcut()
                if last_hid_error(kvm):
                    raise MonitorStartError("hid_failed", f"快捷鍵 HID 錯誤: {last_hid_error(kvm)}")

        if observation.reliable and observation.state == MarkerState.PREPARING:
            preparing()

        deadline = time.monotonic() + start_timeout_sec
        while True:
            decision = await asyncio.to_thread(observe_latest_round_frame, kvm, gate)
            if decision.state == MarkerState.MONITORING:
                if on_monitoring is not None:
                    on_monitoring()
                ok = True
                return {"state": MarkerState.MONITORING.value, "capacity": int(decision.capacity)}
            if decision.state == MarkerState.REVIEW:
                raise _review("等待監控中")
            if decision.state == MarkerState.PREPARING:
                preparing()
            if time.monotonic() >= deadline:
                raise MonitorStartError(
                    "monitor_not_started",
                    f"{start_timeout_sec:g} 秒內未見監控中")
            await asyncio.sleep(POLL_INTERVAL_SEC)
    except MonitorStartError as exc:
        log(str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "monitor_start")
