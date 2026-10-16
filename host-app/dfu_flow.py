"""DFU input 1.0: find every checkbox in the DFU window, then enter SNs by channel.

Channels come from the device list. The window is located with a window template,
checkboxes with the checked/unchecked templates only, and channels 1..N are the
checkboxes read top to bottom, then left to right. Any count other than the
configured channel count refuses the round before a single SN is typed.
"""
import asyncio

import cv2
import numpy as np

from auto_flow import _load_template, locate
from hid_helpers import center, last_hid_error
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
CHECKBOX_THRESHOLD = 0.80
FOCUS_DELAY = 0.30
SN_MAX_LENGTH = 64
ROW_TOLERANCE = 0.5  # fraction of a checkbox height that still counts as the same row


class DfuError(Exception):
    """A refused or failed DFU operation (input or OK). ``code`` is a protocol error code;
    ``entered`` lists slots typed so far."""

    def __init__(self, code, message, entered=()):
        super().__init__(message)
        self.code = code
        self.entered = sorted(entered)


def parse_items(items, channels):
    """Return sorted ``[(slot, sn)]``; raise ``DfuError(invalid_argument)`` before any HID action."""
    if not isinstance(items, list) or not items:
        raise DfuError("invalid_argument", "items must be a non-empty list")
    parsed = {}
    for entry in items:
        if not isinstance(entry, dict) or set(entry) != {"slot", "sn"}:
            raise DfuError("invalid_argument", "each item needs exactly slot and sn")
        slot, sn = entry["slot"], entry["sn"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= channels:
            raise DfuError("invalid_argument", f"slot must be an integer 1..{channels}")
        if slot in parsed:
            raise DfuError("invalid_argument", f"slot {slot} is repeated")
        if not isinstance(sn, str) or not 1 <= len(sn) <= SN_MAX_LENGTH:
            raise DfuError("invalid_argument", f"sn must be 1..{SN_MAX_LENGTH} characters")
        parsed[slot] = sn
    return sorted(parsed.items())


def _find_matches(gray, template, threshold):
    """Non-overlapping peaks of ``template`` in ``gray`` at or above ``threshold``, best first."""
    th, tw = template.shape[:2]
    scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(scores >= threshold)
    peaks = sorted(zip(scores[ys, xs], xs, ys), reverse=True)
    kept = []
    for score, x, y in peaks:
        if any(abs(x - kx) < tw / 2 and abs(y - ky) < th / 2 for _s, kx, ky in kept):
            continue
        kept.append((float(score), int(x), int(y)))
    return [(score, x, y, tw, th) for score, x, y in kept]


def _intersects(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def find_checkboxes(frame, window_box, checked, unchecked, threshold=CHECKBOX_THRESHOLD):
    """Every checkbox inside the window as ``[{state, score, box}]`` in channel order (1..N)."""
    wx, wy, ww, wh = window_box
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[wy:wy + wh, wx:wx + ww]
    found = []
    for state, template in (("checked", checked), ("unchecked", unchecked)):
        for score, x, y, w, h in _find_matches(gray, template, threshold):
            found.append({"state": state, "score": score, "box": (wx + x, wy + y, w, h)})
    # A single box cannot be both states; the stronger match wins.
    found.sort(key=lambda box: box["score"], reverse=True)
    kept = []
    for candidate in found:
        if not any(_intersects(candidate["box"], other["box"]) for other in kept):
            kept.append(candidate)
    kept.sort(key=lambda box: (box["box"][1], box["box"][0]))

    ordered, row, row_y = [], [], None
    for candidate in kept:
        y, h = candidate["box"][1], candidate["box"][3]
        if row_y is None or y - row_y > ROW_TOLERANCE * h:
            ordered.extend(sorted(row, key=lambda box: box["box"][0]))
            row, row_y = [], y
        row.append(candidate)
    ordered.extend(sorted(row, key=lambda box: box["box"][0]))
    return ordered


def _frame(kvm):
    frame = getattr(kvm, "frame", None)
    return None if frame is None else frame.copy()


def _window_match(frame, template, diagnostics, key, threshold):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    value, left, top, width, height = locate(gray, template, downscale=True)
    box = (left, top, width, height) if left is not None else None
    diagnostics.record(key, value, threshold, frame, box, color=(0, 255, 0))
    return value, box


def _locate_control(frame, window_box, template, key, diagnostics, threshold):
    wx, wy, ww, wh = window_box
    roi = cv2.cvtColor(frame[wy:wy + wh, wx:wx + ww], cv2.COLOR_BGR2GRAY)
    value, left, top, width, height = locate(roi, template, downscale=False)
    box = (wx + left, wy + top, width, height) if left is not None else None
    diagnostics.record(key, value, threshold, frame, box)
    if box is None or value < threshold:
        raise DfuError("hid_failed", f"找不到 {key}（相似度 {value:.2f}）")
    return box


async def _focus_dock(kvm, template, diagnostics, threshold):
    frame = _frame(kvm)
    if frame is None:
        raise DfuError("hid_failed", "沒有 JetKVM 影格")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    value, left, top, width, height = locate(gray, template, downscale=False)
    box = (left, top, width, height) if left is not None else None
    diagnostics.record("dock_icon", value, threshold, frame, box)
    if box is None or value < threshold:
        raise DfuError("hid_failed", f"找不到 dock_icon（相似度 {value:.2f}）")
    await kvm.click(*center(*box))
    await asyncio.sleep(FOCUS_DELAY)
    if last_hid_error(kvm):
        raise DfuError("hid_failed", f"Dock HID 錯誤: {last_hid_error(kvm)}")


def _load(catalog, keys, diagnostics, threshold, log):
    loaded = {}
    for key in keys:
        image = _load_template(catalog, "DFU", key, log, diagnostics, threshold)
        if image is None:
            raise DfuError("hid_failed", f"缺少必要 DFU 模板 {key}")
        loaded[key] = image
    return loaded


def _boxes_or_fail(frame, window, templates, channels, diagnostics):
    boxes = find_checkboxes(frame, window, templates["checkbox_checked"], templates["checkbox_unchecked"])
    diagnostics.record("checkboxes", len(boxes), CHECKBOX_THRESHOLD, frame, None,
                       note="expected {}".format(channels))
    if len(boxes) != channels:
        raise DfuError("hid_failed", f"DFU 視窗找到 {len(boxes)} 個 checkbox，設備設定為 {channels} 個通道")
    return boxes


def _mismatches(boxes, wanted):
    """Checkboxes whose state differs from the request: checked for requested channels, unchecked otherwise."""
    result = []
    for channel, box in enumerate(boxes, 1):
        desired = "checked" if channel in wanted else "unchecked"
        if box["state"] != desired:
            result.append((channel, box["box"]))
    return result


async def run_dfu_input(kvm, channels, items, template_root=None, threshold=MATCH_THRESHOLD,
                        log=print, diagnostics_key="DFU"):
    """Set checkboxes for the requested channels, then type each SN. Never clicks OK or sends the shortcut.

    Returns ``{"entered": [slot, ...]}``. Raises ``DfuError`` with the protocol error code;
    no SN is typed unless every checkbox was found and set correctly.
    """
    serials = parse_items(items, channels)
    wanted = {slot for slot, _sn in serials}
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    entered = []
    ok = False
    try:
        templates = _load(catalog, ["dock_icon", "window", "checkbox_checked", "checkbox_unchecked",
                                    "input"], diagnostics, threshold, log)
        if hasattr(kvm, "rpc_errors"):
            kvm.rpc_errors.clear()
        await _focus_dock(kvm, templates["dock_icon"], diagnostics, threshold)

        frame = _frame(kvm)
        if frame is None:
            raise DfuError("hid_failed", "Dock 前景化後沒有 JetKVM 影格")
        _, window = _window_match(frame, templates["window"], diagnostics, "window", threshold)
        if window is None:
            raise DfuError("hid_failed", "找不到 DFU_window")
        boxes = _boxes_or_fail(frame, window, templates, channels, diagnostics)

        for _channel, box in _mismatches(boxes, wanted):
            await kvm.click(*center(*box))
        if last_hid_error(kvm):
            raise DfuError("hid_failed", f"checkbox HID 錯誤: {last_hid_error(kvm)}")
        await asyncio.sleep(FOCUS_DELAY)

        # Verify; allow exactly one correction pass for boxes still out of state.
        for attempt in range(2):
            verify = _frame(kvm)
            if verify is None:
                raise DfuError("hid_failed", "checkbox 複驗沒有 JetKVM 影格")
            _, verified_window = _window_match(verify, templates["window"], diagnostics,
                                               f"window_verify{attempt + 1}", threshold)
            if verified_window is None:
                raise DfuError("hid_failed", "checkbox 複驗找不到 DFU_window")
            boxes = _boxes_or_fail(verify, verified_window, templates, channels, diagnostics)
            remaining = _mismatches(boxes, wanted)
            if not remaining:
                frame, window = verify, verified_window
                break
            if attempt == 1:
                raise DfuError("hid_failed", "checkbox 第二次複驗仍不符")
            for _channel, box in remaining:
                await kvm.click(*center(*box))
            if last_hid_error(kvm):
                raise DfuError("hid_failed", f"checkbox 重試 HID 錯誤: {last_hid_error(kvm)}")
            await asyncio.sleep(FOCUS_DELAY)

        input_box = _locate_control(frame, window, templates["input"], "input", diagnostics, threshold)
        await kvm.click(*center(*input_box))
        for slot, sn in serials:
            await kvm.type_text(sn)
            await kvm.press_enter()
            if last_hid_error(kvm):
                raise DfuError("hid_failed", f"SN 輸入 HID 錯誤: {last_hid_error(kvm)}", entered)
            entered.append(slot)
        ok = True
        return {"entered": entered}
    except DfuError as exc:
        log(str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "dfu_input")


async def run_dfu_ok(kvm, template_root=None, threshold=MATCH_THRESHOLD, log=print, diagnostics_key="DFU"):
    """Bring the DFU window forward and click OK once. Returns ``{"action": "ok"}``.

    Raises ``DfuError`` (hid_failed) when the Dock icon, window or OK button cannot be found or HID fails.
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False
    try:
        templates = _load(catalog, ["dock_icon", "window", "button"], diagnostics, threshold, log)
        if hasattr(kvm, "rpc_errors"):
            kvm.rpc_errors.clear()
        await _focus_dock(kvm, templates["dock_icon"], diagnostics, threshold)

        frame = _frame(kvm)
        if frame is None:
            raise DfuError("hid_failed", "Dock 前景化後沒有 JetKVM 影格")
        _, window = _window_match(frame, templates["window"], diagnostics, "window", threshold)
        if window is None:
            raise DfuError("hid_failed", "找不到 DFU_window")
        button_box = _locate_control(frame, window, templates["button"], "button", diagnostics, threshold)
        await kvm.click(*center(*button_box))
        if last_hid_error(kvm):
            raise DfuError("hid_failed", f"OK HID 錯誤: {last_hid_error(kvm)}")
        ok = True
        return {"action": "ok"}
    except DfuError as exc:
        log(str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "ok")
