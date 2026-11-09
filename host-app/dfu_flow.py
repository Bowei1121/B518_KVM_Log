"""DFU input 1.0: find every checkbox in the DFU window, then enter SNs by channel.

Channels come from the device list. The window is located with a window template,
checkboxes with the checked/unchecked templates (plus a blue-fill fallback for the
macOS checked state), and channels 1..N are the first N checkboxes read top to
bottom, then left to right. The ATE window may show more slots than the device's
channels (e.g. a 7-slot DFU window on a 4-channel round): the extra boxes are kept
unchecked. Extra boxes are only accepted once DFU_region.json is narrowed from the
full-screen default; otherwise any count other than the channel count refuses the
round (a stray checkbox outside the window would shift every channel). Fewer
checkboxes than channels always refuses the round before any SN is typed.
"""
import asyncio
import json

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

# macOS checked checkbox = solid accent-blue square with a white tick. Over a JetKVM frame the
# grayscale checked template can miss it (the slot panel behind it is tinted per slot), so a
# blue blob the size of the unchecked template also counts as a checked box.
BLUE_MIN_B = 180
BLUE_MIN_B_MINUS_R = 110
BLUE_MIN_B_MINUS_G = 40
BLUE_MIN_FILL = 0.45
BLUE_MIN_SIDE = 6
BLUE_SIZE_RANGE = (0.4, 1.3)  # blob side relative to the unchecked template side
BLUE_ASPECT_RANGE = (0.8, 1.2)  # Log Solution folder icons are ~1.25+ wide, so they stay out


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


def _blue_checked(region_bgr, template_shape):
    """Checked-looking blue squares in ``region_bgr`` as ``[(x, y, w, h)]`` template-sized boxes."""
    th, tw = template_shape[:2]
    pixels = region_bgr.astype(np.int16)
    b, g, r = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    mask = ((b >= BLUE_MIN_B) & (b - r >= BLUE_MIN_B_MINUS_R) & (b - g >= BLUE_MIN_B_MINUS_G)).astype(np.uint8)
    count, _labels, stats, _centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    low = max(BLUE_MIN_SIDE, BLUE_SIZE_RANGE[0] * min(tw, th))
    high = BLUE_SIZE_RANGE[1] * max(tw, th)
    region_h, region_w = mask.shape
    boxes = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if not (low <= w <= high and low <= h <= high):
            continue
        if not BLUE_ASPECT_RANGE[0] <= w / h <= BLUE_ASPECT_RANGE[1] or area / (w * h) < BLUE_MIN_FILL:
            continue
        # Report a template-sized box centred on the blob so the click lands in the middle.
        bx = min(max(x + w // 2 - tw // 2, 0), max(region_w - tw, 0))
        by = min(max(y + h // 2 - th // 2, 0), max(region_h - th, 0))
        boxes.append((bx, by, tw, th))
    return boxes


def find_checkboxes(frame, window_box, checked, unchecked, threshold=CHECKBOX_THRESHOLD):
    """Every checkbox inside the window as ``[{state, score, box}]`` in channel order (1..N)."""
    wx, wy, ww, wh = window_box
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[wy:wy + wh, wx:wx + ww]
    for template in (checked, unchecked):
        # matchTemplate raises (or matches backwards) when the area is smaller than the template.
        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            raise FlowError("hid_failed", f"DFU region {gray.shape[1]}x{gray.shape[0]} is smaller than a checkbox template")
    found = []
    for state, template in (("checked", checked), ("unchecked", unchecked)):
        for score, x, y, w, h in _find_matches(gray, template, threshold):
            found.append({"state": state, "score": score, "box": (wx + x, wy + y, w, h)})
    if frame.ndim == 3:
        # Fallback scores sit just below any template hit, so a real template match always wins.
        for x, y, w, h in _blue_checked(frame[wy:wy + wh, wx:wx + ww], unchecked.shape):
            found.append({"state": "checked", "score": threshold - 1e-6, "box": (wx + x, wy + y, w, h)})
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


REGION_FILE = "DFU_region.json"
MAX_COORD = 20000


DEFAULT_REGION = {"x": 0, "y": 0, "width": 1920, "height": 1080}


def create_default_region(path):
    """First DFU action without a region file: write the full-screen default (edit it to narrow the area)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_REGION, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # read-only template folder: still use the default for this run
    return tuple(DEFAULT_REGION[k] for k in ("x", "y", "width", "height"))


def load_region(catalog):
    """Optional fixed DFU window rectangle ``(x, y, w, h)`` in screen pixels, or ``None``.

    ``DFU/DFU_region.json`` = ``{"x": X, "y": Y, "width": W, "height": H}`` at the field screen
    (1920x1080, 100%). The window template's background changes with the ATE mode, so when this file
    exists the window template is not used at all: every DFU control is searched only inside the
    rectangle and nothing outside it is ever matched or clicked. A missing file is created with the
    full-screen default on the first DFU action.
    """
    path = catalog.device_dir("DFU") / REGION_FILE
    if not path.is_file():
        return create_default_region(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        x, y, width, height = (data[k] for k in ("x", "y", "width", "height"))
        ok = (all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y, width, height))
              and 0 <= x <= MAX_COORD and 0 <= y <= MAX_COORD and 0 < width <= MAX_COORD and 0 < height <= MAX_COORD)
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise FlowError("hid_failed", f"Invalid {REGION_FILE}: need integer x, y and positive width, height")
    return x, y, width, height


def _clip(frame, region):
    x, y, w, h = region
    frame_h, frame_w = frame.shape[:2]
    if x >= frame_w or y >= frame_h:
        raise FlowError("hid_failed", f"{REGION_FILE} lies outside the JetKVM frame")
    return (x, y, min(w, frame_w - x), min(h, frame_h - y))


def _window(session, frame, template, key, fixed=None):
    """The DFU region: the fixed rectangle when configured, else the window template match box."""
    if fixed is not None:
        return _clip(frame, fixed)
    return session.find(key, template, frame, downscale=True, color=(0, 255, 0),
                        message="DFU_window not found", check_threshold=False)


def _control(session, frame, region, template, key):
    return session.find(key, template, frame, within=region, downscale=False)


def _allows_extra_boxes(frame, region):
    """Trust extra slots only when the clipped DFU region excludes part of the frame."""
    frame_h, frame_w = frame.shape[:2]
    return _clip(frame, region) != (0, 0, frame_w, frame_h)


def _verify_slots(first_slots, boxes):
    """Require each locked channel to uniquely match the same ordered checkbox."""
    for index, (x, y, w, h) in enumerate(first_slots):
        tolerance = min(3, min(w, h) / 4)
        matches = []
        for candidate, box in enumerate(boxes):
            bx, by, bw, bh = box["box"]
            if (abs(x + w / 2 - bx - bw / 2) <= tolerance
                    and abs(y + h / 2 - by - bh / 2) <= tolerance):
                matches.append(candidate)
        if matches != [index]:
            raise FlowError("hid_failed", "DFU checkbox slot identity changed during verification")


def _boxes_or_fail(frame, window, templates, channels, diagnostics, suffix="", allow_extra=False):
    boxes = find_checkboxes(frame, window, templates["checkbox_checked"], templates["checkbox_unchecked"])
    expected = ("at least {}" if allow_extra else "{}").format(channels)
    expected_zh = ("至少 {}" if allow_extra else "{}").format(channels)
    diagnostics.record("checkboxes" + suffix, len(boxes), CHECKBOX_THRESHOLD, frame, None,
                       note={"en": "expected " + expected, "zh": "預期 {} 個 checkbox".format(expected_zh)})
    if len(boxes) < channels or (len(boxes) > channels and not allow_extra):
        hint = "" if allow_extra or len(boxes) < channels else f"; set {REGION_FILE} to the DFU window to allow extra ATE slots"
        raise FlowError("hid_failed", f"DFU window has {len(boxes)} checkboxes; device is configured for {channels} channels{hint}")
    return boxes


def _mismatches(boxes, wanted):
    """Checkboxes whose state differs from the request: checked for requested channels, unchecked otherwise.

    Boxes past the configured channels (extra ATE slots) are never requested, so they are kept unchecked.
    """
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
    fixed = None
    ok = False
    try:
        fixed = load_region(catalog)
        templates = _load(session, ["dock_icon", "checkbox_checked", "checkbox_unchecked", "input"])
        frame = session.frame()
        _box, frame = await session.focus_dock_fresh(
            "dock_icon", templates["dock_icon"], frame, message=None, settle=FOCUS_DELAY)

        def find_controls(image, suffix):
            found = _window(session, image, templates.get("window"), "window" + suffix, fixed)
            return found, _boxes_or_fail(image, found, templates, channels, diagnostics, suffix,
                                        _allows_extra_boxes(image, found))

        # Nothing was typed or clicked yet: one retry on fresh frames is safe. The Dock is not clicked again.
        frame, (window, boxes) = await session.match_with_retry(frame, find_controls)

        first_slots = [box["box"] for box in boxes[:channels]]

        for _channel, box in _mismatches(boxes, wanted):
            await session.click(box, failure="Checkbox HID failed")
        await asyncio.sleep(FOCUS_DELAY)

        # Verify; allow exactly one correction pass for boxes still out of state.
        for attempt in range(2):
            verify = session.frame(missing="No JetKVM frame available during checkbox verification")
            try:
                verified_window = _window(session, verify, templates.get("window"), f"window_verify{attempt + 1}", fixed)
            except FlowError as exc:
                if exc.score is None:
                    raise
                raise FlowError("hid_failed", "DFU_window not found during checkbox verification") from exc
            boxes = _boxes_or_fail(verify, verified_window, templates, channels, diagnostics,
                                   allow_extra=_allows_extra_boxes(verify, verified_window))
            _verify_slots(first_slots, boxes)
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
        fixed = load_region(catalog)
        templates = _load(session, ["dock_icon", "button"])
        frame = session.frame()
        _box, frame = await session.focus_dock_fresh(
            "dock_icon", templates["dock_icon"], frame, message=None, settle=FOCUS_DELAY)

        def find_button(image, suffix):
            found = _window(session, image, templates.get("window"), "window" + suffix, fixed)
            return _control(session, image, found, templates["button"], "button" + suffix)

        frame, button_box = await session.match_with_retry(frame, find_button)
        await session.click(button_box, failure="OK HID failed", after=lambda: session.park_pointer(frame))
        ok = True
        return {"action": "ok"}
    except FlowError as exc:
        log(exc.code, str(exc))
        raise
    finally:
        diagnostics.finalize(ok, "ok")
