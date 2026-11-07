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
import logging
import time

from hid_session import FlowError, HidSession, neutral_pointer_position  # noqa: F401
from match_diagnostics import MatchDiagnostics, draw_locator_zoom
from log_solution_heartbeat import HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE
from log_solution_reader import MarkerState, explain_app_frame
from reading_stats import read_latest_app_frame_recorded
from round_frame_consumer import observe_latest_round_frame
from template_catalog import TemplateCatalog

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.80
MONITOR_CONFIRM_SEC = 3.0  # wait for Log Solution to show on screen after the Dock click
MONITOR_START_TIMEOUT_SEC = 10.0  # wait for Monitoring after Cmd+Shift+M
POLL_INTERVAL_SEC = 0.05


def _busy(state):
    return FlowError("log_solution_busy", f"Log Solution is {state.value}; no shortcut sent")


def _explain_note(explanation):
    """English / Chinese sentences with the locating summary and the numbers of a failing cell."""
    explanation = explanation or {}
    locate = explanation.get("locate")
    en = zh = ""
    if locate:
        en = "; " + locate["summary"]
        zh = "；定位：" + locate["summary"]
    failure = explanation.get("failure")
    if failure:
        joined = "; ".join("({},{},{}) -> {} d={}".format(*sample["bgr"], sample["nearest"], sample["distance"])
                           for sample in failure["samples"])
        en += f"; cell {failure['slot']} corner samples BGR: {joined}"
        zh += f"；第 {failure['slot']} 格四角取樣 BGR（最近契約色、距離）：{joined}"
    return en, zh


def _record_reading(diagnostics, key, frame, threshold, note, matched):
    """Annotated Log Solution reading picture plus the 4x locator zoom; diagnostics only, so callers swallow errors."""
    try:
        explanation = explain_app_frame(frame)
    except Exception:
        explanation = None
        logger.exception("Log Solution reading explanation failed; saving the picture unannotated")
    en_extra, zh_extra = _explain_note(explanation)
    box = None
    if matched and explanation is not None and explanation.get("band"):
        box = tuple(int(round(v)) for v in explanation["band"])
    note = {"en": note["en"] + en_extra, "zh": note["zh"] + zh_extra}
    diagnostics.record(
        key, 1.0 if matched else None, threshold, frame=frame, box=box, note=note, explanation=explanation)
    if explanation is not None:
        zoom = draw_locator_zoom(frame, explanation)
        if zoom is not None:
            diagnostics.record("log_solution_locators_zoom", None, threshold, frame=zoom, matched=matched,
                               plain=True, note=note)


async def _wait_for_reliable_reading(kvm, timeout_sec, stats=None, last_unreliable=None, last_reliable=None,
                                     deadline=None):
    """``(observation, last_reason)``: the newest reliable reading within timeout_sec, or None with
    the reason of the last unreliable one (``no_frame`` when there was no picture).

    ``last_unreliable`` (optional dict) receives ``{"frame": ...}`` with the newest unreliable picture, so the
    caller can keep it as diagnostics when the wait times out. ``last_reliable`` likewise receives the
    picture of the reliable reading that ended the wait.

    ``deadline`` (absolute ``time.monotonic()``) makes the wait strict: nothing is read once it has passed, and
    a reading finished after it is not accepted, so a reliable picture that shows up late never counts.
    """
    strict = deadline is not None
    if deadline is None:
        deadline = time.monotonic() + timeout_sec
    last_reason = "no_frame"
    while True:
        if strict and time.monotonic() >= deadline:
            return None, last_reason
        frame, observation = read_latest_app_frame_recorded(kvm, stats)
        if strict and time.monotonic() > deadline:
            return None, last_reason
        if observation is not None and observation.reliable:
            if frame is not None and last_reliable is not None:
                last_reliable["frame"] = frame
            return observation, ""
        if frame is not None and last_unreliable is not None:
            last_unreliable["frame"] = frame
        if observation is not None:
            last_reason = observation.reason or "unreadable"
        if time.monotonic() >= deadline:
            return None, last_reason
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def click_dock_icon(kvm, frame, session, confirm_sec=None):
    """Click the shared Dock icon once, park the pointer, wait for fresh frames (within ``confirm_sec`` of the click). Returns ``(clicked, score)``; no click when the icon is not on screen.

    A missing template or a HID error raises ``FlowError(hid_failed)``.
    """
    dock_path = session.catalog.path("COMMON", "log_solution_dock_icon")
    template = session.load("COMMON", "log_solution_dock_icon", record_missing=False)
    if template is None:
        raise FlowError("hid_failed", f"Missing Log Solution Dock icon template: {dock_path}")

    try:
        box, _fresh = await session.focus_dock_fresh(
            "log_solution_dock_icon", template, frame, message="Dock icon not found", within_sec=confirm_sec)
    except FlowError as exc:
        if exc.score is None:
            raise
        return False, exc.score
    return True, box.score


async def bring_log_solution_forward(kvm, diagnostics_key, template_root=None, threshold=MATCH_THRESHOLD, stats=None):
    """check's visibility recovery: one Dock click, no keys. Returns False when the Dock icon is not on screen.

    ``diagnostics_key`` is the device ID; match diagnostics are kept per device (ADR-0007).
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False
    try:
        frame, _observation = read_latest_app_frame_recorded(kvm, stats)
        if frame is None:
            raise FlowError("hid_failed", "No JetKVM frame available")
        session = HidSession(kvm, template_root, diagnostics, threshold=threshold)
        ok, _score = await click_dock_icon(kvm, frame, session)
        return ok
    finally:
        diagnostics.finalize(ok, "check_dock_recovery")


def _review(where):
    return FlowError("log_solution_review", f"Log Solution requires review ({where}); monitoring was not started")


def _kvm_lost_waiting():
    return FlowError("kvm_connect_failed", "KVM connection lost while waiting for monitoring")


async def run_monitor_start(kvm, gate, diagnostics_key, template_root=None, threshold=MATCH_THRESHOLD,
                            confirm_sec=MONITOR_CONFIRM_SEC, start_timeout_sec=MONITOR_START_TIMEOUT_SEC,
                            log=print, on_preparing=None, on_shortcut=None):
    """Start Log Solution monitoring. Returns ``{"state": "monitoring", "capacity": n}``.

    ``gate`` is the device's RoundFrameGate; a Monitoring reading arms its round once a start was requested
    (Preparing or the shortcut, through the callbacks below). ``diagnostics_key`` is the
    device ID; match diagnostics are kept per device (ADR-0007). Raises ``FlowError`` with the
    protocol error code otherwise. Callbacks record the round-start request; a timeout does not undo them.
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    session = HidSession(kvm, template_root, diagnostics, threshold=threshold, log=log)
    ok = False

    def preparing():
        if on_preparing is not None:
            on_preparing()

    try:
        frame, observation = read_latest_app_frame_recorded(kvm, gate.stats)
        if frame is None:
            raise FlowError("hid_failed", "No JetKVM frame available")
        if observation.reliable and observation.state == MarkerState.MONITORING:
            # Busy refusal is not "seen monitoring for this round": the request stays unanswered.
            raise _busy(observation.state)
        if observation.reliable and observation.state == MarkerState.REVIEW:
            raise _review("before start")
        if observation.may_send_hid:
            clicked, score = await click_dock_icon(kvm, frame, session, confirm_sec)
            if not clicked:
                raise FlowError("log_solution_not_found",
                                f"Log Solution Dock icon not found (similarity {score:.2f})")
            unreliable = {}
            reliable = {}
            # One confirm window from the click: the fresh-frame wait above is part of it, not added to it.
            observation, last_reason = await _wait_for_reliable_reading(
                kvm, confirm_sec, gate.stats, unreliable, reliable,
                deadline=session.dock_clicked_at + confirm_sec)
            if observation is not None:
                try:  # best-effort diagnostics: the annotated reading of the screen we are about to act on
                    _record_reading(
                        diagnostics, "log_solution_reading", reliable.get("frame"), threshold,
                        {"en": f"Log Solution read after Dock click: state {observation.state.value}, "
                               f"capacity {observation.capacity}, statuses {','.join(observation.statuses)}",
                         "zh": f"點擊 Dock 後讀到 Log Solution：狀態 {observation.state.value}，"
                               f"容量 {observation.capacity}，各格 {','.join(observation.statuses)}"}, True)
                except Exception as exc:
                    log(f"log_solution_reading diagnostics not saved: {exc!r}")
            if observation is None:
                try:  # best-effort diagnostics: a write failure must not replace log_solution_not_found
                    _record_reading(
                        diagnostics, "confirm_last_unreliable", unreliable.get("frame"), threshold,
                        {"en": f"No reliable Log Solution reading within {confirm_sec:g} s after Dock click"
                                    f" (last reading: {last_reason})",
                              "zh": f"點擊 Dock 後 {confirm_sec:g} 秒內沒有可靠的 Log Solution 判讀"
                                    f"（最後判讀：{last_reason}）"}, False)
                except Exception as exc:
                    log(f"confirm_last_unreliable diagnostics not saved: {exc!r}")
                raise FlowError("log_solution_not_found",
                                f"Log Solution not found within {confirm_sec:g} s after Dock click; no shortcut sent"
                                f" (last reading: {last_reason})")
            if observation.state == MarkerState.MONITORING:
                raise _busy(observation.state)
            if observation.state == MarkerState.REVIEW:
                raise _review("after Dock click")
            if observation.may_open_round:
                async def note_shortcut():
                    if on_shortcut is not None:
                        on_shortcut()

                await session.press_command_shift_m(failure="Shortcut HID failed", after=note_shortcut)

        if observation.reliable and observation.state == MarkerState.PREPARING:
            preparing()

        deadline = time.monotonic() + start_timeout_sec
        while True:
            if not kvm.is_healthy():
                raise _kvm_lost_waiting()
            decision = await asyncio.to_thread(observe_latest_round_frame, kvm, gate)
            if decision.kind == "frozen":
                # A dead KVM also freezes its last picture: report the lost connection, not the freeze.
                if not kvm.is_healthy():
                    gate.stream.release_kvm_lost_latch(decision.verdict)
                    raise _kvm_lost_waiting()
                gate.stream.mark_frozen_delivered()
                raise FlowError("frame_frozen", decision.detail or "Log Solution frame stream is frozen")
            if decision.kind == "heartbeat_stopped":
                # The display stopped while waiting: no later reading can count as monitoring.
                raise FlowError(HEARTBEAT_STOPPED, HEARTBEAT_STOPPED_MESSAGE)
            if decision.state == MarkerState.MONITORING:
                ok = True
                return {"state": MarkerState.MONITORING.value, "capacity": int(decision.capacity)}
            if decision.state == MarkerState.REVIEW:
                raise _review("while waiting for monitoring")
            if decision.state == MarkerState.PREPARING:
                preparing()
            if time.monotonic() >= deadline:
                raise FlowError(
                    "monitor_not_started",
                    f"Monitoring not observed within {start_timeout_sec:g} s")
            await asyncio.sleep(POLL_INTERVAL_SEC)
    except FlowError as exc:
        log(exc.code, str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "monitor_start")
