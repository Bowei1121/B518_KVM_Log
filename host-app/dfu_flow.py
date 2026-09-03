"""DFU's profile-aware visual state machines.

The TCP/UI layer only supplies a device number and the original input payload.
This module owns profile lookup, slot validation, visual matching and HID order.
"""
import asyncio
import json
from pathlib import Path

import cv2

from auto_flow import _load_template, locate
from match_diagnostics import MatchDiagnostics
from ocr_sn import read_sn
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
MATCH_MARGIN = 0.05
FOCUS_DELAY = 0.30
PROFILES = {"4slot": 4, "7slot": 7}


def parse_sn_payload(payload, slot_count):
    """Return sorted ``[(slot, serial_number)]`` or fail before any HID action."""
    text = str(payload or "").strip()
    if not text:
        raise ValueError("DFU input 缺少 slot:SN payload")
    parsed = {}
    for item in text.split(","):
        item = item.strip()
        if item.count(":") != 1:
            raise ValueError("DFU SN 格式錯誤，應為 slot:SN")
        slot_text, sn = (part.strip() for part in item.split(":", 1))
        if not slot_text.isdigit() or not sn:
            raise ValueError("DFU SN 格式錯誤，slot 必須是編號且 SN 不可為空")
        slot = int(slot_text)
        if slot < 1 or slot > slot_count:
            raise ValueError("DFU slot {} 超出 {} profile 範圍".format(slot, slot_count))
        if slot in parsed:
            raise ValueError("DFU slot {} 重複指定".format(slot))
        parsed[slot] = sn
    return sorted(parsed.items())


def load_profile(template_root, device_no):
    """Load the explicit station-to-DFU-profile map, never infer from the screen."""
    path = Path(template_root) / "device_profiles.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        profile = data["DFU"][str(device_no)]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("DFU profile 設定無效或缺少設備 {}：{}".format(device_no, path)) from exc
    if profile not in PROFILES:
        raise ValueError("DFU profile 必須為 4slot 或 7slot，實際為 {}".format(profile))
    return profile, PROFILES[profile]


def validate_input_request(template_root, device_no, payload):
    """Validate the external request before the UI opens a KVM/HID session."""
    profile, slot_count = load_profile(template_root, device_no)
    return profile, parse_sn_payload(payload, slot_count)


def _center(box):
    x, y, width, height = box
    return int(x + width / 2), int(y + height / 2)


def _has_hid_error(kvm):
    errors = getattr(kvm, "rpc_errors", [])
    return errors[-1] if errors else None


def _frame(kvm, diagnostics, threshold, log):
    if kvm.frame is None:
        note = "沒有 JetKVM 影格"
        diagnostics.record("frame", None, threshold, note=note)
        log(note)
        return None
    return kvm.frame.copy()


def _match(frame, template, downscale=False):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return locate(gray, template, downscale=downscale)


def _record(diagnostics, key, value, threshold, frame, box, note="", color=(0, 200, 255)):
    diagnostics.record(key, value, threshold, frame, box, note=note, color=color)


def _window_match(frame, template, diagnostics, key, threshold):
    value, left, top, width, height = _match(frame, template, downscale=True)
    box = (left, top, width, height) if left is not None else None
    _record(diagnostics, key, value, threshold, frame, box, color=(0, 255, 0))
    return value, box


def _slot_rows(frame, window_box, templates, profile, prefix, diagnostics, threshold):
    """Find every explicitly named slot anchor and return the row to its right."""
    wx, wy, ww, wh = window_box
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    window_gray = gray[wy:wy + wh, wx:wx + ww]
    rows = {}
    for slot in range(1, PROFILES[profile] + 1):
        key = "{}slot{}_{}".format(prefix, slot, profile)
        value, left, top, width, height = locate(window_gray, templates[key], downscale=False)
        global_box = (wx + left, wy + top, width, height) if left is not None else None
        _record(diagnostics, key, value, threshold, frame, global_box)
        if left is None or value < threshold:
            raise ValueError("找不到 {}（相似度 {:.2f}）".format(key, value))
        # The row begins at the anchor and extends to the right edge of the matched window.
        row_y = max(wy, wy + top - max(12, height // 2))
        row_h = min(wy + wh, wy + top + height + max(12, height // 2)) - row_y
        row_x = wx + left
        rows[slot] = (row_x, row_y, wx + ww - row_x, row_h)
    return rows


def _best_state(frame, row_box, checked, unchecked, diagnostics, slot, threshold):
    x, y, width, height = row_box
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    row = gray[y:y + height, x:x + width]
    options = []
    for state, template in (("checked", checked), ("unchecked", unchecked)):
        value, left, top, w, h = locate(row, template, downscale=False)
        box = (x + left, y + top, w, h) if left is not None else None
        _record(diagnostics, "checkbox_slot{}_{}".format(slot, state), value, threshold, frame, box)
        options.append((value, state, box))
    options.sort(reverse=True, key=lambda entry: entry[0])
    best, state, box = options[0]
    second = options[1][0]
    if box is None or best < threshold or best - second < MATCH_MARGIN:
        raise ValueError("slot {} checkbox 狀態不明確（{:.2f}/{:.2f}）".format(slot, best, second))
    return state, box, best, second


def _locate_control(frame, window_box, template, key, diagnostics, threshold):
    wx, wy, ww, wh = window_box
    roi = cv2.cvtColor(frame[wy:wy + wh, wx:wx + ww], cv2.COLOR_BGR2GRAY)
    value, left, top, width, height = locate(roi, template, downscale=False)
    box = (wx + left, wy + top, width, height) if left is not None else None
    _record(diagnostics, key, value, threshold, frame, box)
    if box is None or value < threshold:
        raise ValueError("找不到 {}（相似度 {:.2f}）".format(key, value))
    return box


async def _focus_dock(kvm, template, diagnostics, key, threshold, log):
    frame = _frame(kvm, diagnostics, threshold, log)
    if frame is None:
        raise ValueError("沒有 JetKVM 影格")
    value, left, top, width, height = _match(frame, template, downscale=False)
    box = (left, top, width, height) if left is not None else None
    _record(diagnostics, key, value, threshold, frame, box)
    if box is None or value < threshold:
        raise ValueError("找不到 {}（相似度 {:.2f}）".format(key, value))
    await kvm.click(*_center(box))
    await asyncio.sleep(FOCUS_DELAY)
    if _has_hid_error(kvm):
        raise ValueError("Dock HID 錯誤: {}".format(_has_hid_error(kvm)))


def _load(catalog, keys, diagnostics, threshold, log):
    loaded = {}
    for key in keys:
        image = _load_template(catalog, "DFU", key, log, diagnostics, threshold)
        if image is None:
            raise ValueError("缺少必要 DFU 模板")
        loaded[key] = image
    return loaded


async def run_dfu_input(kvm, device_no, payload, template_root=None, threshold=MATCH_THRESHOLD, log=print):
    """Correct all profile checkboxes, enter indexed SNs, click OK once, then shortcut Log."""
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, "DFU")
    result = {"ok": False}

    def finish(note=""):
        if note:
            result["error"] = note
            log(note)
        diagnostics.finalize(result["ok"], "dfu_input")
        return result

    try:
        profile, serials = validate_input_request(catalog.root, device_no, payload)
        count = PROFILES[profile]
        templates = _load(catalog, ["dock_icon", "window", "checkbox_checked", "checkbox_unchecked", "input", "button"] +
                          ["slot{}_{}".format(i, profile) for i in range(1, count + 1)], diagnostics, threshold, log)
        if hasattr(kvm, "rpc_errors"):
            kvm.rpc_errors.clear()
        await _focus_dock(kvm, templates["dock_icon"], diagnostics, "dock_icon", threshold, log)
        frame = _frame(kvm, diagnostics, threshold, log)
        if frame is None:
            raise ValueError("Dock 前景化後沒有 JetKVM 影格")
        _, window = _window_match(frame, templates["window"], diagnostics, "window", threshold)
        if window is None:
            raise ValueError("找不到 DFU_window")
        rows = _slot_rows(frame, window, templates, profile, "", diagnostics, threshold)
        wanted = {slot for slot, _sn in serials}
        mismatches = []
        for slot, row in rows.items():
            current, box, _best, _second = _best_state(frame, row, templates["checkbox_checked"], templates["checkbox_unchecked"], diagnostics, slot, threshold)
            if current != ("checked" if slot in wanted else "unchecked"):
                mismatches.append((slot, box))
        for _slot, box in mismatches:
            await kvm.click(*_center(box))
        if _has_hid_error(kvm):
            raise ValueError("checkbox HID 錯誤: {}".format(_has_hid_error(kvm)))
        await asyncio.sleep(FOCUS_DELAY)

        # Verify, then make exactly one retry for the slots still out of state.
        for attempt in range(2):
            verify = _frame(kvm, diagnostics, threshold, log)
            if verify is None:
                raise ValueError("checkbox 複驗沒有 JetKVM 影格")
            _, verified_window = _window_match(verify, templates["window"], diagnostics, "window_verify{}".format(attempt + 1), threshold)
            if verified_window is None:
                raise ValueError("checkbox 複驗找不到 DFU_window")
            verified_rows = _slot_rows(verify, verified_window, templates, profile, "", diagnostics, threshold)
            remaining = []
            for slot, row in verified_rows.items():
                current, box, _best, _second = _best_state(verify, row, templates["checkbox_checked"], templates["checkbox_unchecked"], diagnostics, slot, threshold)
                if current != ("checked" if slot in wanted else "unchecked"):
                    remaining.append((slot, box))
            if not remaining:
                frame, window = verify, verified_window
                break
            if attempt == 1:
                raise ValueError("checkbox 第二次複驗仍不符")
            for _slot, box in remaining:
                await kvm.click(*_center(box))
            if _has_hid_error(kvm):
                raise ValueError("checkbox 重試 HID 錯誤: {}".format(_has_hid_error(kvm)))
            await asyncio.sleep(FOCUS_DELAY)

        input_box = _locate_control(frame, window, templates["input"], "input", diagnostics, threshold)
        button_box = _locate_control(frame, window, templates["button"], "button", diagnostics, threshold)
        await kvm.click(*_center(input_box))
        for _slot, sn in serials:
            await kvm.type_text(sn)
            await kvm.press_enter()
        await kvm.click(*_center(button_box))
        await kvm.press_command_shift_m()
        if _has_hid_error(kvm):
            raise ValueError("HID 錯誤: {}".format(_has_hid_error(kvm)))
        result.update(ok=True, profile=profile, serials=serials)
        return finish()
    except ValueError as exc:
        return finish(str(exc))


def _classify_log_row(frame, row_box, templates, slot, diagnostics, threshold):
    x, y, width, height = row_box
    row = cv2.cvtColor(frame[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
    candidates = []
    for state in ("testing", "pass", "fail", "notest"):
        value, left, top, w, h = locate(row, templates["log_" + state], downscale=False)
        box = (x + left, y + top, w, h) if left is not None else None
        _record(diagnostics, "log_slot{}_{}".format(slot, state), value, threshold, frame, box)
        candidates.append((value, state, box))
    candidates.sort(reverse=True, key=lambda entry: entry[0])
    best, state, box = candidates[0]
    if box is None or best < threshold or best - candidates[1][0] < MATCH_MARGIN:
        raise ValueError("Log slot {} 狀態不明確（{:.2f}/{:.2f}）".format(slot, best, candidates[1][0]))
    # Status is near the left-side anchor; OCR only the right-most row area to avoid it.
    sn_roi = frame[y:y + height, x + int(width * 0.45):x + width]
    sn = "" if state in ("testing", "notest") else read_sn(sn_roi)
    diagnostics.record("log_slot{}_ocr".format(slot), None, threshold, sn_roi, note=sn or "沒有 OCR SN")
    if state in ("pass", "fail") and not sn:
        raise ValueError("Log slot {} {} 但 OCR 找不到 SN".format(slot, state))
    return state, sn


async def run_dfu_check(kvm, device_no, template_root=None, threshold=MATCH_THRESHOLD, log=print):
    """Focus the separate Log monitor and return profile-complete visual results."""
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, "DFU")
    result = {"ok": False, "testing": False, "rows": []}

    def finish(note=""):
        if note:
            result["error"] = note
            log(note)
        diagnostics.finalize(result["ok"], "dfu_check")
        return result

    try:
        profile, count = load_profile(catalog.root, device_no)
        keys = ["log_dock_icon", "log_window", "log_testing", "log_pass", "log_fail", "log_notest"]
        keys += ["log_slot{}_{}".format(i, profile) for i in range(1, count + 1)]
        templates = _load(catalog, keys, diagnostics, threshold, log)
        if hasattr(kvm, "rpc_errors"):
            kvm.rpc_errors.clear()
        await _focus_dock(kvm, templates["log_dock_icon"], diagnostics, "log_dock_icon", threshold, log)
        frame = _frame(kvm, diagnostics, threshold, log)
        if frame is None:
            raise ValueError("Log 前景化後沒有 JetKVM 影格")
        _, window = _window_match(frame, templates["log_window"], diagnostics, "log_window", threshold)
        if window is None:
            raise ValueError("找不到 DFU_log_window")
        rows = _slot_rows(frame, window, templates, profile, "log_", diagnostics, threshold)
        interpreted = []
        for slot in range(1, count + 1):
            state, sn = _classify_log_row(frame, rows[slot], templates, slot, diagnostics, threshold)
            interpreted.append((slot, sn, state))
        if any(state == "testing" for _slot, _sn, state in interpreted):
            result.update(ok=True, testing=True, rows=[])
        else:
            result.update(ok=True, rows=interpreted, profile=profile)
        return finish()
    except ValueError as exc:
        return finish(str(exc))
