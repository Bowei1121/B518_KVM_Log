"""Reads one Log Solution screen from one JetKVM frame, display contract 1.3.

Answers what a single reading, or two consecutive fresh readings, decide: reliability, Log state,
capacity, the 20 result cells, started channels with their evidence, and whether the screen is
busy, may open a new round, or may send HID. Rules that need round progress live in
round_frame_consumer.
"""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Optional, Tuple

from log_solution_heartbeat import unreadable_heartbeat

import cv2
import numpy as np


CONTRACT_VERSION = "1.3"
LOCATOR_SIZE = 22
LEFT_LOCATOR = (0, 2, 3)
RIGHT_LOCATOR = (320, 2, 11)
LOCATOR_INSET_SIZE = 8
LOCATOR_DISTANCE = RIGHT_LOCATOR[0] - LEFT_LOCATOR[0]
# Offsets inside each locator: the white hole's centre, and the middle of the black band
# above-left of the hole (half the inset), where a sample stays clear of blended edges.
LEFT_HOLE_CENTRE = LEFT_LOCATOR[2] + LOCATOR_INSET_SIZE / 2.0
RIGHT_HOLE_CENTRE = RIGHT_LOCATOR[2] + LOCATOR_INSET_SIZE / 2.0
LEFT_BLACK_MID = LEFT_LOCATOR[2] / 2.0
RIGHT_BLACK_MID = RIGHT_LOCATOR[2] / 2.0
HOLE_DISTANCE = RIGHT_LOCATOR[0] + RIGHT_HOLE_CENTRE - (LEFT_LOCATOR[0] + LEFT_HOLE_CENTRE)
MARKER_ORIGIN = (250, 0)
HEARTBEAT_ORIGIN = (278, 0)
MARKER_CELL_SIZE = 10
MARKER_GAP = 2
MARKER_QUIET = 2
LAYOUT_ORIGIN = (130, 2)
RESULT_FIRST_Y = 34
RESULT_ROW_STEP = 27
RESULT_CELL_WIDTH = 34
RESULT_CELL_HEIGHT = 26
RESULT_COLUMNS = 10
BLACK_MAX = 80
WHITE_MIN = 176
# The App background between the locator row and the result band, sampled straight above slot 1's
# corner samples. Every channel at or above GUTTER_MIN is at least 26 from WAITING (217, 217, 217),
# the palest status, so a cover that passes cannot read as a status.
GUTTER_POINTS = ((6, 29), (17, 29), (28, 29))
GUTTER_MIN = 232
COLOR_TOLERANCE = 20
# Deep-ring slack under scale 1.4: at most this far from the corner colour counts as compression.
# Below WAITING↔STOPPED (45), so an exact STOPPED cover on a WAITING cell still rejects.
DEEP_RING_LOW_SCALE_SLACK = 42
# NOTE: part of this WAITING->STOPPED shift may have come from decoding JetKVM frames with BT.709 instead of
# BT.601 (fixed in the colorspace PR, JETKVM_DECODE_COLORSPACE). Re-evaluate this slack on site before removing it.
TERMINAL_STATUSES = frozenset(("PASS", "FAIL", "NOTEST", "TIMEOUT", "STOPPED"))


class MarkerState(str, Enum):
    STANDBY = "standby"
    PREPARING = "preparing"
    MONITORING = "monitoring"
    REVIEW = "review"
    COMPLETE = "complete"
    MANUAL_STOP_COMPLETE = "manual_stop_complete"


# Left-top, right-top, left-bottom, right-bottom. 1 = black. Two black and two white only.
PATTERNS = {
    MarkerState.STANDBY: (1, 0, 0, 1),
    MarkerState.PREPARING: (0, 1, 0, 1),
    MarkerState.MONITORING: (1, 1, 0, 0),
    MarkerState.REVIEW: (1, 0, 1, 0),
    MarkerState.COMPLETE: (0, 1, 1, 0),
    MarkerState.MANUAL_STOP_COMPLETE: (0, 0, 1, 1),
}

# BGR values copied from App KVM_DISPLAY_CONTRACT / STATUS_COLOURS.
STATUS_COLORS = {
    "PASS": (0, 239, 0),
    "FAIL": (0, 0, 255),
    "TESTING": (0, 255, 255),
    "NOTEST": (241, 75, 240),
    "WAITING": (217, 217, 217),
    "COMPLETING": (255, 199, 130),
    "STOPPED": (191, 191, 191),
    "TIMEOUT": (0, 153, 255),
}

# Screen states grouped by what a reading lets the host do with them.
FINISHED_STATES = frozenset((MarkerState.COMPLETE, MarkerState.MANUAL_STOP_COMPLETE))
BUSY_STATES = frozenset((MarkerState.PREPARING, MarkerState.MONITORING, MarkerState.REVIEW))
OPEN_STATES = frozenset((MarkerState.STANDBY,)) | FINISHED_STATES
CONFIRMATION_REQUIRED = "confirmation_required"


class ScreenKind(str, Enum):
    """What one reading decides for the reply on its own; round progress decides the rest."""

    UNKNOWN = "unknown"
    PAUSED = "paused"
    WAITING = "waiting"


@dataclass(frozen=True)
class FrameObservation:
    reliable: bool
    state: Optional[MarkerState] = None
    capacity: int = 0
    statuses: Tuple[str, ...] = ()
    reason: str = ""
    scale: float = 0.0
    app_origin: Tuple[float, ...] = ()
    heartbeat: Tuple[Optional[bool], ...] = ()

    @property
    def results_complete(self):
        return (self.finished and self.capacity > 0 and len(self.statuses) == self.capacity
                and all(status in TERMINAL_STATUSES for status in self.statuses))

    @property
    def finished(self):
        """A reliable Complete or ManualStopComplete screen: the round's results are on it."""
        return self.reliable and self.state in FINISHED_STATES

    @property
    def busy(self):
        """A reliable Preparing, Monitoring or Review screen: a round is under way or awaiting confirmation."""
        return self.reliable and self.state in BUSY_STATES

    @property
    def may_open_round(self):
        """A reliable Standby, Complete or ManualStopComplete screen: monitor_start may send the shortcut."""
        return self.reliable and self.state in OPEN_STATES

    @property
    def may_send_hid(self):
        """Not busy. An unreadable screen still allows the Dock click, which is also the visibility recovery."""
        return not self.busy


NEAR_SCALE_MIN, NEAR_SCALE_MAX = 0.5, 4.0  # outer boxes this close to a locator are worth explaining when rejected
MAX_NEAR_REJECTED = 20
MAX_PAIR_CHECKS = 50


def _scan_locators(frame, report=None):
    """``(left, right)`` locator candidates ``(x, y, scale, hole_cx, hole_cy)``.

    ``report`` (optional dict) receives the same scan for diagnostics, without changing the answer:
    ``left_boxes`` / ``right_boxes`` (outer box [x, y, w, h] per candidate, same order) and ``near_rejected``
    (``{"box", "scale", "reason"}`` for near-square dark boxes rejected for scale range, shape or hole).
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, BLACK_MAX, 255, cv2.THRESH_BINARY_INV)
    contours, hierarchy = cv2.findContours(dark, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    left, right = [], []
    if report is not None:
        report.update(left_boxes=[], right_boxes=[], near_rejected=[])
    if hierarchy is None:
        return left, right
    hierarchy = hierarchy[0]
    for index, contour in enumerate(contours):
        x, y, width, height = cv2.boundingRect(contour)
        scale = width / float(LOCATOR_SIZE)
        reason = None
        if scale < 0.70 or scale > 3.25:
            reason = "scale_range"
        elif abs(width - height) > max(2, scale * 0.10):
            reason = "not_square"
        if reason is not None:
            if (report is not None and NEAR_SCALE_MIN <= scale <= NEAR_SCALE_MAX
                    and abs(width - height) <= max(2, 0.3 * width)):
                report["near_rejected"].append({"box": [x, y, width, height], "scale": scale, "reason": reason})
            continue
        child = hierarchy[index][2]
        expected_size = LOCATOR_INSET_SIZE * scale
        accepted = False
        saw_child = saw_size = False
        while child >= 0:
            ix, iy, iw, ih = cv2.boundingRect(contours[child])
            saw_child = True
            if (abs(iw - expected_size) <= max(4, scale * 0.35)
                    and abs(ih - expected_size) <= max(4, scale * 0.35)):
                saw_size = True
                for expected, target, key in ((LEFT_LOCATOR[2], left, "left_boxes"),
                                              (RIGHT_LOCATOR[2], right, "right_boxes")):
                    offset = expected * scale
                    if (abs((ix - x) - offset) <= max(3, scale * 0.35)
                            and abs((iy - y) - offset) <= max(3, scale * 0.35)):
                        target.append((x, y, scale, ix + iw / 2.0, iy + ih / 2.0))
                        accepted = True
                        if report is not None:
                            report[key].append([x, y, width, height])
            child = hierarchy[child][0]
        if not accepted and report is not None:
            reason = "hole_offset" if saw_size else ("hole_size" if saw_child else "no_hole")
            report["near_rejected"].append({"box": [x, y, width, height], "scale": scale, "reason": reason})
    if report is not None:
        report["near_rejected"].sort(key=lambda item: (abs(item["box"][2] - item["box"][3]),
                                                       abs(item["scale"] - 1.0)))
        del report["near_rejected"][MAX_NEAR_REJECTED:]
    return left, right


def _locator_candidates(frame):
    return _scan_locators(frame)


def _pair_locators(left, right, checks=None):
    """Valid left/right pairs ``(score, x, y, hole_scale, left_index, right_index)``.

    ``checks`` (optional list) receives every examined pair with its numbers and the failed conditions
    (``dx``, ``dy``, ``scale_mismatch``); at most MAX_PAIR_CHECKS are kept.
    """
    pairs = []
    for li, (lx, ly, ls, lcx, lcy) in enumerate(left):
        for ri, (rx, ry, rs, rcx, rcy) in enumerate(right):
            scale = (rx - lx) / float(LOCATOR_DISTANCE)
            expected_dx = LOCATOR_DISTANCE * scale
            tolerance = max(3.0, expected_dx * 0.035)
            dx_ok = abs((rx - lx) - expected_dx) <= tolerance
            dy_ok = abs(ry - ly) <= max(3.0, scale * 0.12)
            scale_ok = (abs(ls - rs) <= scale * 0.08 and abs(((ls + rs) / 2.0) - scale) <= scale * 0.08)
            if checks is not None and len(checks) < MAX_PAIR_CHECKS:
                checks.append({"left": li, "right": ri, "dx": float(rx - lx), "expected_dx": float(expected_dx),
                               "dy": float(ry - ly), "scale_diff": float(abs(ls - rs)),
                               "failed": [name for name, ok in (("dx", dx_ok), ("dy", dy_ok),
                                                                ("scale_mismatch", scale_ok)) if not ok]})
            if dx_ok and dy_ok and scale_ok:
                # Hole centres, not outer corners, anchor the origin: a dark desktop neighbour
                # widens the outer box, while blur moves the hole centre by about a pixel at most.
                hole_scale = (rcx - lcx) / HOLE_DISTANCE
                x = ((lcx - (LEFT_LOCATOR[0] + LEFT_HOLE_CENTRE) * hole_scale)
                     + (rcx - (RIGHT_LOCATOR[0] + RIGHT_HOLE_CENTRE) * hole_scale)) / 2.0
                y = ((lcy - (LEFT_LOCATOR[1] + LEFT_HOLE_CENTRE) * hole_scale)
                     + (rcy - (RIGHT_LOCATOR[1] + RIGHT_HOLE_CENTRE) * hole_scale)) / 2.0
                pairs.append((abs((rx - lx) - expected_dx) + abs(ry - ly), x, y, hole_scale, li, ri))
    return pairs


def _locate_app(frame):
    left, right = _locator_candidates(frame)
    pairs = _pair_locators(left, right)
    if not pairs:
        return None
    _, x, y, scale, _li, _ri = min(pairs, key=lambda item: item[0])
    return x, y, scale


def _median_patch(frame, x, y, radius):
    cx, cy = int(round(x)), int(round(y))
    half = max(1, int(round(radius)))
    patch = frame[max(0, cy - half):min(frame.shape[0], cy + half + 1),
                  max(0, cx - half):min(frame.shape[1], cx + half + 1)]
    if patch.size == 0:
        return None
    return np.median(patch.reshape(-1, 3), axis=0)


def _pixel(frame, x, y):
    cx, cy = int(round(x)), int(round(y))
    if 0 <= cx < frame.shape[1] and 0 <= cy < frame.shape[0]:
        return frame[cy, cx].astype(float)
    return None


def _marker_bit(sample):
    if sample is None:
        return None
    value = float(np.mean(sample))
    if value <= BLACK_MAX:
        return 1
    if value >= WHITE_MIN:
        return 0
    return None


def _sample_status(frame, x, y, scale):
    # Search along each border's normal, including near both corners. Stay inside
    # this cell so a neighbouring cell's border cannot stand in for ours.
    borders = []
    for offset in (3, 17, 31):
        borders.extend(((x + offset * scale, y + 0.5 * scale, 0, 1),
                        (x + offset * scale, y + 25.5 * scale, 0, 1)))
    for offset in (3, 13, 23):
        borders.extend(((x + 0.5 * scale, y + offset * scale, 1, 0),
                        (x + 33.5 * scale, y + offset * scale, 1, 0)))
    reach = max(1, int(round(scale)))
    x_lo, x_hi = int(np.floor(x)), int(np.ceil(x + 34 * scale)) - 1
    y_lo, y_hi = int(np.floor(y)), int(np.ceil(y + 26 * scale)) - 1
    for px, py, dx, dy in borders:
        bx, by = int(round(px)), int(round(py))
        if not (0 <= bx < frame.shape[1] and 0 <= by < frame.shape[0]):
            return None
        candidates = [(bx + d * dx, by + d * dy) for d in range(-reach, reach + 1)
                      if x_lo <= bx + d * dx <= x_hi and y_lo <= by + d * dy <= y_hi]
        if not any(0 <= cx < frame.shape[1] and 0 <= cy < frame.shape[0]
                   and np.max(frame[cy, cx]) <= 160 for cx, cy in candidates):
            return None

    def _classify(sample):
        if sample is None:
            return None
        distances = sorted((float(np.linalg.norm(sample.astype(float) - np.asarray(color))), name)
                           for name, color in STATUS_COLORS.items())
        if distances[0][0] <= COLOR_TOLERANCE:
            return distances[0][1]
        if float(np.linalg.norm(sample.astype(float))) <= 35:
            return "OUTSIDE"
        return None

    # Four interior corner patches avoid the centred status text and cell border.
    corners = ((6, 5), (28, 5), (6, 21), (28, 21))
    votes = []
    for px, py in corners:
        label = _classify(_median_patch(frame, x + px * scale, y + py * scale,
                                        max(1, scale * 0.7)))
        if label is None:
            return None
        votes.append(label)
    if len(set(votes)) != 1:
        return None
    corner_vote = votes[0]

    # Deeper ring (~2.5–3 u): a thick occluder that still leaves original colour farther in.
    d = 2.5 if scale >= 1.4 else max(3.0, 3.0 / scale)
    deep_ring = ((6, d), (17, d), (28, d), (6, 26 - d), (17, 26 - d), (28, 26 - d),
                 (d, 5), (d, 13), (d, 21), (34 - d, 5), (34 - d, 13), (34 - d, 21))
    deep_corner = (np.asarray(STATUS_COLORS[corner_vote], dtype=float)
                   if corner_vote != "OUTSIDE" else None)
    for px, py in deep_ring:
        sample = (_median_patch(frame, x + px * scale, y + py * scale, scale * 0.7)
                  if scale >= 1.4 else _pixel(frame, x + px * scale, y + py * scale))
        label = _classify(sample)
        if label is None:
            continue
        if label != corner_vote:
            # Under 1.4 px per unit (a 1920x1080 Mac at 100%) one pixel carries the H.264 block's
            # colour shift: WAITING (217) reads as STOPPED (191). A sample still this close to the
            # corner colour is that shift, not a differently coloured cover.
            if (scale < 1.4 and deep_corner is not None and label != "OUTSIDE"
                    and float(np.linalg.norm(sample.astype(float) - deep_corner)) <= DEEP_RING_LOW_SCALE_SLACK):
                continue
            return None

    # Option 1 — shallow original-colour ring for insets 2–5, just inside the black border.
    # A disagreeing *status* colour rejects only when the sample is clearly far from the
    # corner colour (border blends of WAITING↔STOPPED otherwise false-trigger).
    # Black / OOC cover against a coloured corner:
    #   - nominal scale >= 1.9 (locator threshold 1.85): mid-edge 1.7u black rejects
    #     (bf88620 semantics); also if all 3 shallow points on one side are black.
    #   - any scale >= 1.0: >=4 black hits across sides (multi-side reinforcement).
    # Below ~1.9, e=1.7 can land on the cell's own black border after resampling, so
    # single-side / mid-edge black gates stay off there.
    if scale >= 1.0:
        e = 1.7
        mid_edge = ((17, e), (17, 26 - e), (e, 13), (34 - e, 13))
        shallow_sides = (((17, e), (6, e), (28, e)),
                         ((17, 26 - e), (6, 26 - e), (28, 26 - e)),
                         ((e, 13), (e, 5), (e, 21)),
                         ((34 - e, 13), (34 - e, 5), (34 - e, 21)))
        corner_color = np.asarray(STATUS_COLORS[corner_vote], dtype=float) if corner_vote != "OUTSIDE" else None
        outside_hits = 0
        for side in shallow_sides:
            side_outside_hits = 0
            for px, py in side:
                sample = _pixel(frame, x + px * scale, y + py * scale)
                label = _classify(sample)
                if label is None or label == corner_vote:
                    continue
                if label == "OUTSIDE":
                    if corner_vote != "OUTSIDE":
                        outside_hits += 1
                        side_outside_hits += 1
                        if scale >= 1.85 and (px, py) in mid_edge:
                            return None
                    continue
                if corner_color is not None:
                    dist_corner = float(np.linalg.norm(sample.astype(float) - corner_color))
                    # Border blends can classify as a neighbouring grey status.
                    if dist_corner <= COLOR_TOLERANCE + 55:
                        continue
                return None
            if scale >= 1.85 and side_outside_hits == 3:
                return None
        if outside_hits >= 4:
            return None

    if corner_vote == "OUTSIDE":
        # Out-of-capacity cells are solid black: the centre and mid-edges must be black too,
        # so a partial occluder cannot turn one into a status cell or back.
        for px, py in ((17, 13), (17, 4), (17, 22), (4, 13), (30, 13)):
            sample = _median_patch(frame, x + px * scale, y + py * scale, max(1, scale * 0.7))
            if sample is None or float(np.linalg.norm(sample.astype(float))) > 35:
                return None
    return corner_vote



def _marker_cells(frame, x0, y0, scale, origin):
    """Four 2x2 cells at ``origin`` in top-left, top-right, bottom-left, bottom-right order.

    Each is 1 for black, 0 for white, None when the sample is neither.
    """
    cells = []
    for row in range(2):
        for column in range(2):
            px = x0 + (origin[0] + MARKER_QUIET + column * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale
            py = y0 + (origin[1] + MARKER_QUIET + row * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale
            cells.append(_marker_bit(_median_patch(frame, px, py, max(1, scale * 1.2))))
    return tuple(cells)


def inspect_app_frame(frame, received_at, now=None, max_age=1.0):
    """Decode one BGR JetKVM frame using contract 1.3 geometry and color samples.

    ``heartbeat`` carries the four heartbeat cells (True black, False white, None unreadable)
    whenever the locators were found. It does not decide reliability; the heartbeat rule lives
    with the caller.
    """
    if now is None:
        now = time.monotonic()
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        return FrameObservation(False, reason="invalid_frame")
    if received_at is None or received_at > now + 0.05 or now - received_at > max_age:
        return FrameObservation(False, reason="stale_frame")
    located = _locate_app(frame)
    if located is None:
        return FrameObservation(False, reason="locators_missing_or_ambiguous")
    x0, y0, scale = located
    required_width = x0 + 10 * RESULT_CELL_WIDTH * scale
    required_height = y0 + (RESULT_FIRST_Y + RESULT_CELL_HEIGHT) * scale
    if x0 < 0 or y0 < 0 or required_width > frame.shape[1] or required_height > frame.shape[0]:
        return FrameObservation(False, reason="app_band_cropped", scale=scale, app_origin=(x0, y0))

    # Black samples sit mid-band: under 1 px per unit a patch 1 unit from the outer edge
    # always takes in blended edge pixels.
    locator_points = ((LEFT_LOCATOR[0] + LEFT_HOLE_CENTRE, LEFT_LOCATOR[1] + LEFT_HOLE_CENTRE),
                      (LEFT_LOCATOR[0] + LEFT_BLACK_MID, LEFT_LOCATOR[1] + LEFT_BLACK_MID),
                      (RIGHT_LOCATOR[0] + RIGHT_BLACK_MID, RIGHT_LOCATOR[1] + RIGHT_BLACK_MID),
                      (RIGHT_LOCATOR[0] + RIGHT_HOLE_CENTRE, RIGHT_LOCATOR[1] + RIGHT_HOLE_CENTRE))
    locator_values = [_median_patch(frame, x0 + px * scale, y0 + py * scale, max(1, scale * 0.8))
                      for px, py in locator_points]
    if (any(value is None for value in locator_values)
            or np.mean(locator_values[0]) < WHITE_MIN
            or np.mean(locator_values[1]) > BLACK_MAX
            or np.mean(locator_values[2]) > BLACK_MAX
            or np.mean(locator_values[3]) < WHITE_MIN):
        return FrameObservation(False, reason="locator_orientation_or_contrast_invalid",
                                scale=scale, app_origin=(x0, y0))
    # A look-alike square paired with the real right locator shifts the origin a few units; whatever
    # hides the real left locator then also covers this gutter and slot 1, so it must still be App
    # background before any cell is read.
    gutter = [_median_patch(frame, x0 + gx * scale, y0 + gy * scale, max(1, scale * 1.2))
              for gx, gy in GUTTER_POINTS]
    if any(value is None or float(np.min(value)) < GUTTER_MIN for value in gutter):
        return FrameObservation(False, reason="locator_orientation_or_contrast_invalid",
                                scale=scale, app_origin=(x0, y0))

    heartbeat = tuple(None if bit is None else bool(bit)
                      for bit in _marker_cells(frame, x0, y0, scale, HEARTBEAT_ORIGIN))
    bits = _marker_cells(frame, x0, y0, scale, MARKER_ORIGIN)
    if any(bit is None for bit in bits):
        return FrameObservation(False, reason="marker_low_contrast_or_occluded",
                                scale=scale, app_origin=(x0, y0), heartbeat=heartbeat)
    state = next((key for key, pattern in PATTERNS.items() if pattern == tuple(bits)), None)
    if state is None:
        return FrameObservation(False, reason="unknown_marker_pattern", scale=scale,
                                app_origin=(x0, y0), heartbeat=heartbeat)

    layout_bits = tuple(_marker_bit(_median_patch(
        frame, x0 + (LAYOUT_ORIGIN[0] + MARKER_QUIET
                      + column * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale,
        y0 + (LAYOUT_ORIGIN[1] + MARKER_QUIET + MARKER_CELL_SIZE / 2.0) * scale,
        max(1, scale * 1.2))) for column in range(2))
    rows = {(1, 0): 1, (0, 1): 2}.get(layout_bits)
    if rows is None:
        return FrameObservation(False, state, reason="layout_marker_missing_or_invalid",
                                scale=scale, app_origin=(x0, y0), heartbeat=heartbeat)
    required_height = y0 + (RESULT_FIRST_Y + (rows - 1) * RESULT_ROW_STEP
                            + RESULT_CELL_HEIGHT) * scale
    if required_height > frame.shape[0]:
        return FrameObservation(False, state, reason="app_band_cropped", scale=scale,
                                app_origin=(x0, y0), heartbeat=heartbeat)

    statuses = []
    outside_seen = False
    for slot in range(1, rows * RESULT_COLUMNS + 1):
        row, column = divmod(slot - 1, RESULT_COLUMNS)
        sx = x0 + column * RESULT_CELL_WIDTH * scale
        sy = y0 + (RESULT_FIRST_Y + row * RESULT_ROW_STEP) * scale
        status = _sample_status(frame, sx, sy, scale)
        if status is None or status == "UNKNOWN":
            return FrameObservation(False, state, reason="result_cell_unknown_{}".format(slot),
                                    scale=scale, app_origin=(x0, y0), heartbeat=heartbeat)
        if status == "OUTSIDE":
            outside_seen = True
            continue
        if outside_seen:
            return FrameObservation(False, state, reason="non_contiguous_capacity", scale=scale,
                                    app_origin=(x0, y0), heartbeat=heartbeat)
        statuses.append(status)
    if not statuses:
        return FrameObservation(False, state, reason="capacity_not_visible", scale=scale,
                                app_origin=(x0, y0), heartbeat=heartbeat)
    capacity = len(statuses)
    if rows == 2 and capacity <= RESULT_COLUMNS:
        return FrameObservation(False, state, reason="layout_capacity_mismatch",
                                scale=scale, app_origin=(x0, y0), heartbeat=heartbeat)
    return FrameObservation(True, state, capacity, tuple(statuses), scale=scale,
                            app_origin=(x0, y0), heartbeat=heartbeat)


STATUS_CORNER_POINTS = ((6, 5), (28, 5), (6, 21), (28, 21))  # the four interior corner samples of a result cell


def _cell_box(x0, y0, scale, slot):
    row, column = divmod(slot - 1, RESULT_COLUMNS)
    return [x0 + column * RESULT_CELL_WIDTH * scale, y0 + (RESULT_FIRST_Y + row * RESULT_ROW_STEP) * scale,
            RESULT_CELL_WIDTH * scale, RESULT_CELL_HEIGHT * scale]


def _explain_cell_samples(frame, x, y, scale):
    """The four corner samples of one result cell: BGR value, nearest contract colour and its distance."""
    samples = []
    for px, py in STATUS_CORNER_POINTS:
        fx, fy = x + px * scale, y + py * scale
        value = _median_patch(frame, fx, fy, max(1, scale * 0.7))
        if value is None:
            continue
        nearest, distance = min(((name, float(np.linalg.norm(value.astype(float) - np.asarray(color))))
                                 for name, color in STATUS_COLORS.items()), key=lambda item: item[1])
        samples.append({"point": [fx, fy], "bgr": [int(round(v)) for v in value],
                        "nearest": nearest, "distance": round(distance, 1)})
    return samples


def _explain_locating(frame):
    """Locator candidates, pair checks, the chosen pair and near-miss boxes of one frame, as plain data."""
    report, checks = {}, []
    left, right = _scan_locators(frame, report)
    pairs = _pair_locators(left, right, checks)
    chosen = min(pairs, key=lambda item: item[0]) if pairs else None

    def candidates(items, boxes):
        return [{"box": [float(v) for v in box], "scale": float(item[2]),
                 "hole_centre": [float(item[3]), float(item[4])]} for item, box in zip(items, boxes)]

    selected = None if chosen is None else {
        "left": chosen[4], "right": chosen[5], "origin": [float(chosen[1]), float(chosen[2])],
        "scale": float(chosen[3])}
    rejected = [{"box": [float(v) for v in item["box"]], "scale": float(item["scale"]), "reason": item["reason"]}
                for item in report["near_rejected"]]
    summary = "locators: left {}, right {}, pairs {} ({} valid), selected {}, near-miss boxes {}".format(
        len(left), len(right), len(checks), len(pairs),
        "none" if selected is None else "origin=({:.1f},{:.1f}) scale={:.3f}".format(*selected["origin"], selected["scale"]),
        len(rejected))
    reasons = {}
    for item in rejected:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    if reasons:
        summary += " (" + ", ".join("{}:{}".format(k, v) for k, v in sorted(reasons.items())) + ")"
    return {"left": candidates(left, report["left_boxes"]), "right": candidates(right, report["right_boxes"]),
            "pairs": checks, "valid_pairs": len(pairs), "selected": selected, "rejected": rejected,
            "summary": summary}


def _explain_sample_points(x0, y0, scale):
    """Frame points of gate 3 (gutter), the state marker cells and the heartbeat cells."""
    def cells(origin):
        return [[x0 + (origin[0] + MARKER_QUIET + column * (MARKER_CELL_SIZE + MARKER_GAP) + MARKER_CELL_SIZE / 2.0) * scale,
                 y0 + (origin[1] + MARKER_QUIET + row * (MARKER_CELL_SIZE + MARKER_GAP) + MARKER_CELL_SIZE / 2.0) * scale]
                for row in range(2) for column in range(2)]
    return {"gutter": [[x0 + gx * scale, y0 + gy * scale] for gx, gy in GUTTER_POINTS],
            "marker": cells(MARKER_ORIGIN), "heartbeat": cells(HEARTBEAT_ORIGIN)}


def explain_app_frame(frame):
    """Diagnostics of one BGR frame as plain data (JSON-able), for annotated pictures. Never raises on a frame.

    Keys: ``located`` (bool), ``reliable``, ``reason``, ``state``, ``scale``, ``origin`` ([x, y] or None),
    ``locators`` (two [x, y, w, h] boxes), ``band`` (the result-cell band [x, y, w, h]),
    ``cells`` ([{"slot", "box", "status"}], status None for the failing cell) and ``failure``
    (``{"slot", "reason", "samples"}`` when the reading failed on ``result_cell_unknown_N``, else None),
    ``locate`` (candidates, pair checks, chosen pair, near-miss boxes and a one-line ``summary``) and
    ``samples`` (gutter / marker / heartbeat sample points, when located).
    The reading uses the same rules as ``read_snapshot`` (heartbeat included), on this frame alone.
    """
    out = {"located": False, "reliable": False, "reason": "", "state": None, "scale": 0.0, "origin": None,
           "locators": [], "band": None, "cells": [], "failure": None, "locate": None, "samples": None}
    if frame is None or not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        out["reason"] = "invalid_frame"
        return out
    now = time.monotonic()
    observation = unreadable_heartbeat(inspect_app_frame(frame, now, now=now))
    out["reliable"] = bool(observation.reliable)
    out["reason"] = observation.reason
    try:  # the locating story must never hide the reading itself
        out["locate"] = _explain_locating(frame)
    except Exception:
        out["locate"] = None
    out["state"] = None if observation.state is None else observation.state.value
    located = _locate_app(frame)
    if located is None:
        return out
    x0, y0, scale = located
    out.update(located=True, scale=float(scale), origin=[float(x0), float(y0)],
               samples=_explain_sample_points(x0, y0, scale))
    out["locators"] = [[x0 + loc[0] * scale, y0 + loc[1] * scale, LOCATOR_SIZE * scale, LOCATOR_SIZE * scale]
                       for loc in (LEFT_LOCATOR, RIGHT_LOCATOR)]
    if observation.reliable:
        rows = (observation.capacity - 1) // RESULT_COLUMNS + 1
        out["cells"] = [{"slot": slot, "box": _cell_box(x0, y0, scale, slot), "status": status}
                        for slot, status in enumerate(observation.statuses, 1)]
    else:
        prefix = "result_cell_unknown_"
        failing = int(observation.reason[len(prefix):]) if observation.reason.startswith(prefix) else 0
        for slot in range(1, failing):
            box = _cell_box(x0, y0, scale, slot)
            out["cells"].append({"slot": slot, "box": box, "status": _sample_status(frame, box[0], box[1], scale)})
        if failing:
            box = _cell_box(x0, y0, scale, failing)
            out["cells"].append({"slot": failing, "box": box, "status": None})
            out["failure"] = {"slot": failing, "reason": observation.reason,
                              "samples": _explain_cell_samples(frame, box[0], box[1], scale)}
        rows = (failing - 1) // RESULT_COLUMNS + 1 if failing else 1
    last = _cell_box(x0, y0, scale, max(1, (rows - 1) * RESULT_COLUMNS + 1))
    out["band"] = [x0, y0, RESULT_COLUMNS * RESULT_CELL_WIDTH * scale, last[1] + last[3] - y0]
    return out


def read_snapshot(snapshot, now=None):
    """Reading of one ``FrameSnapshot``, or None when there is no picture yet.

    A reliable reading whose heartbeat is not fully readable comes back unreliable (Log Solution not visible),
    so every caller that checks ``reliable`` sees the heartbeat rule (issue #77).
    """
    if snapshot is None:
        return None
    return unreadable_heartbeat(inspect_app_frame(snapshot.image, snapshot.received_monotonic, now=now))


def read_app_frame(kvm):
    """``(snapshot, observation)``. Both are None when no picture has arrived yet."""
    snapshot = kvm.latest_frame()
    return snapshot, read_snapshot(snapshot, now=time.monotonic())


def read_latest_app_frame(kvm):
    """Gate-less reading of the newest frame: ``(frame, observation)``, or ``(None, None)`` with no frame yet."""
    snapshot, observation = read_app_frame(kvm)
    frame = None if snapshot is None else snapshot.image
    return frame, observation


def confirm_activity(previous, sequence, received_at, observation, max_gap=1.0):
    """Pair two fresh frames and return ``(new_previous, [(slot, evidence), ...])``.

    TESTING/COMPLETING are ``testing``. PASS/FAIL are ``result``. Any other status is
    not activity. A slot counts only when both frames are active; testing on either
    frame wins. An unreliable frame resets the pair. A repeated sequence does not.
    """
    if observation is None or not observation.reliable or sequence is None or received_at is None:
        return None, []
    current = {}
    for index, status in enumerate(observation.statuses, 1):
        if status in ("TESTING", "COMPLETING"):
            current[index] = "testing"
        elif status in ("PASS", "FAIL"):
            current[index] = "result"
    active = []
    if previous is not None and sequence > previous[0]:
        gap = received_at - previous[1]
        if 0 <= gap <= max_gap:
            for slot, evidence in sorted(current.items()):
                if slot not in previous[2]:
                    continue
                if "testing" in (evidence, previous[2][slot]):
                    evidence = "testing"
                active.append((slot, evidence))
    if previous is None or sequence > previous[0]:
        previous = (sequence, received_at, current)
    return previous, active


class ActivityTracker:
    """Started channels from consecutive fresh readings. Each consumer owns its own instance."""

    def __init__(self, max_gap=1.0):
        self.max_gap = float(max_gap)
        self.previous = None
        self.last_active = ()

    def reset(self):
        """Forget the pair and the last answer: an unreliable frame or a new stream."""
        self.previous = None
        self.last_active = ()

    def clear_active(self):
        """Forget the last answer only. A repeated frame does not break the pair."""
        self.last_active = ()

    def observe(self, observation, sequence, received_at):
        """Feed one reading and return the channels it confirms as started."""
        self.previous, rows = confirm_activity(self.previous, sequence, received_at, observation, self.max_gap)
        self.last_active = tuple(rows)
        return self.last_active


def reportable_active(state, rows):
    """Started channels a reply may carry. A finished screen never reports started."""
    return () if state in FINISHED_STATES else tuple(rows)


def screen_kind(observation):
    """What one reading decides for the reply: UNKNOWN when unreliable, PAUSED on Review, else WAITING."""
    if not observation.reliable:
        return ScreenKind.UNKNOWN
    if observation.state == MarkerState.REVIEW:
        return ScreenKind.PAUSED
    return ScreenKind.WAITING


