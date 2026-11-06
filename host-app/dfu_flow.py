"""DFU input 1.0: find every checkbox in the DFU window, then enter SNs by channel.

Channels come from the device list. The window is located with a window template,
checkboxes with the checked/unchecked templates only, and channels 1..N are the
checkboxes read top to bottom, then left to right. Any count other than the
configured channel count refuses the round before a single SN is typed.
"""
import asyncio

import cv2
import numpy as np

from hid_session import FlowError, HidSession
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
CHECKBOX_THRESHOLD = 0.80
FOCUS_DELAY = 0.30
SN_MAX_LENGTH = 64
ROW_TOLERANCE = 0.5  # fraction of a checkbox height that still counts as the same row


def parse_items(items, channels):
    """Return sorted ``[(slot, sn)]``; raise ``FlowError(invalid_argument)`` before any HID action."""
    if not isinstance(items, list) or not items:
        raise FlowError("invalid_argument", "items must be a non-empty list")
    parsed = {}
    for entry in items:
        if not isinstance(entry, dict) or set(entry) != {"slot", "sn"}:
            raise FlowError("invalid_argument", "each item needs exactly slot and sn")
        slot, sn = entry["slot"], entry["sn"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= channels:
            raise FlowError("invalid_argument", f"slot must be an integer 1..{channels}")
        if slot in parsed:
            raise FlowError("invalid_argument", f"slot {slot} is repeated")
        if not isinstance(sn, str) or not 1 <= len(sn) <= SN_MAX_LENGTH:
            raise FlowError("invalid_argument", f"sn must be 1..{SN_MAX_LENGTH} characters")
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


def _load(session, keys):
    loaded = {}
    for key in keys:
        image = session.load("DFU", key)
        if image is None:
            raise FlowError("hid_failed", f"Missing required DFU template {key}")
        loaded[key] = image
    return loaded


def _window(session, frame, template, key):
    return session.find(key, template, frame, downscale=True, color=(0, 255, 0),
                        message="DFU_window not found", check_threshold=False)


def search_region(frame, window_box):
    """From the window match's top-left to the frame's right/bottom edge.

    The window template may cover only part of the window (e.g. its top-left corner), so the
    match box can be smaller than the real window; controls lie to its right and below.
    """
    height, width = frame.shape[:2]
    wx, wy = max(0, int(window_box[0])), max(0, int(window_box[1]))
    return (wx, wy, max(1, width - wx), max(1, height - wy))


def _control(session, frame, window_box, template, key):
    """Find a control in the extended region, then once on the whole frame; report the higher score on a miss."""
    try:
        return session.find(key, template, frame, within=search_region(frame, window_box), downscale=False)
    except FlowError as first:
        try:
            return session.find(key, template, frame, downscale=False)
        except FlowError as second:
            scores = [e.score for e in (first, second) if getattr(e, "score", None) is not None]
            error = FlowError("hid_failed", f"{key} not found (similarity {max(scores):.2f})" if scores else str(second))
            if scores:
                error.score = max(scores)
            raise error from second


def _boxes_or_fail(frame, window, templates, channels, diagnostics):
    boxes = find_checkboxes(frame, search_region(frame, window), templates["checkbox_checked"], templates["checkbox_unchecked"])
    diagnostics.record("checkboxes", len(boxes), CHECKBOX_THRESHOLD, frame, None,
                       note={"en": "expected {}".format(channels), "zh": "預期 {} 個 checkbox".format(channels)})
    if len(boxes) != channels:
        raise FlowError("hid_failed", f"DFU window has {len(boxes)} checkboxes; device is configured for {channels} channels")
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

    Returns ``{"entered": [slot, ...]}``. Raises ``FlowError`` with the protocol error code;
    no SN is typed unless every checkbox was found and set correctly.
    """
    serials = parse_items(items, channels)
    wanted = {slot for slot, _sn in serials}
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    session = HidSession(kvm, template_root, diagnostics, threshold=threshold, log=log)
    entered = []
    ok = False
    try:
        templates = _load(session, ["dock_icon", "window", "checkbox_checked", "checkbox_unchecked", "input"])
        frame = session.frame()
        await session.focus_dock("dock_icon", templates["dock_icon"], frame, message=None, settle=FOCUS_DELAY)

        frame = session.frame(missing="No JetKVM frame available after Dock focus")
        window = _window(session, frame, templates["window"], "window")
        boxes = _boxes_or_fail(frame, window, templates, channels, diagnostics)

        for _channel, box in _mismatches(boxes, wanted):
            await session.click(box, failure="Checkbox HID failed")
        await asyncio.sleep(FOCUS_DELAY)

        # Verify; allow exactly one correction pass for boxes still out of state.
        for attempt in range(2):
            verify = session.frame(missing="No JetKVM frame available during checkbox verification")
            try:
                verified_window = _window(session, verify, templates["window"], f"window_verify{attempt + 1}")
            except FlowError as exc:
                if exc.score is None:
                    raise
                raise FlowError("hid_failed", "DFU_window not found during checkbox verification") from exc
            boxes = _boxes_or_fail(verify, verified_window, templates, channels, diagnostics)
            remaining = _mismatches(boxes, wanted)
            if not remaining:
                frame, window = verify, verified_window
                break
            if attempt == 1:
                raise FlowError("hid_failed", "Checkbox state still mismatched on second verification")
            for _channel, box in remaining:
                await session.click(box, failure="Checkbox retry HID failed")
            await asyncio.sleep(FOCUS_DELAY)

        input_box = _control(session, frame, window, templates["input"], "input")
        await session.click(input_box, failure="SN input HID failed", entered=entered)
        for slot, sn in serials:
            await session.type_text(sn, failure="SN input HID failed", entered=entered)
            await session.press_enter(failure="SN input HID failed", entered=entered)
            entered.append(slot)
        ok = True
        return {"entered": entered}
    except FlowError as exc:
        log(exc.code, str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "dfu_input")


async def run_dfu_ok(kvm, template_root=None, threshold=MATCH_THRESHOLD, log=print, diagnostics_key="DFU"):
    """Bring the DFU window forward and click OK once. Returns ``{"action": "ok"}``.

    Raises ``FlowError`` (hid_failed) when the Dock icon, window or OK button cannot be found or HID fails.
    """
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    session = HidSession(kvm, template_root, diagnostics, threshold=threshold, log=log)
    ok = False
    try:
        templates = _load(session, ["dock_icon", "window", "button"])
        frame = session.frame()
        await session.focus_dock("dock_icon", templates["dock_icon"], frame, message=None, settle=FOCUS_DELAY)

        frame = session.frame(missing="No JetKVM frame available after Dock focus")
        window = _window(session, frame, templates["window"], "window")
        button_box = _control(session, frame, window, templates["button"], "button")
        await session.click(button_box, failure="OK HID failed")
        ok = True
        return {"action": "ok"}
    except FlowError as exc:
        log(exc.code, str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "ok")
