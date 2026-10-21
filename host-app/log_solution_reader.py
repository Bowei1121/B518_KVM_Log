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


def _locator_candidates(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(gray, BLACK_MAX, 255, cv2.THRESH_BINARY_INV)
    contours, hierarchy = cv2.findContours(dark, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return [], []
    hierarchy = hierarchy[0]
    left, right = [], []
    for index, contour in enumerate(contours):
        x, y, width, height = cv2.boundingRect(contour)
        scale = width / float(LOCATOR_SIZE)
        if (scale < 0.70 or scale > 3.25 or abs(width - height) > max(2, scale * 0.10)):
            continue
        child = hierarchy[index][2]
        while child >= 0:
            ix, iy, iw, ih = cv2.boundingRect(contours[child])
            expected_size = LOCATOR_INSET_SIZE * scale
            if (abs(iw - expected_size) <= max(4, scale * 0.35)
                    and abs(ih - expected_size) <= max(4, scale * 0.35)):
                for expected, target in ((LEFT_LOCATOR[2], left), (RIGHT_LOCATOR[2], right)):
                    offset = expected * scale
                    if (abs((ix - x) - offset) <= max(3, scale * 0.35)
                            and abs((iy - y) - offset) <= max(3, scale * 0.35)):
                        target.append((x, y, scale, ix + iw / 2.0, iy + ih / 2.0))
            child = hierarchy[child][0]
    return left, right


def _locate_app(frame):
    left, right = _locator_candidates(frame)
    pairs = []
    for lx, ly, ls, lcx, lcy in left:
        for rx, ry, rs, rcx, rcy in right:
            scale = (rx - lx) / float(LOCATOR_DISTANCE)
            expected_dx = LOCATOR_DISTANCE * scale
            tolerance = max(3.0, expected_dx * 0.035)
            if (abs((rx - lx) - expected_dx) <= tolerance
                    and abs(ry - ly) <= max(3.0, scale * 0.12)
                    and abs(ls - rs) <= scale * 0.08
                    and abs(((ls + rs) / 2.0) - scale) <= scale * 0.08):
                # Hole centres, not outer corners, anchor the origin: a dark desktop neighbour
                # widens the outer box, while blur moves the hole centre by about a pixel at most.
                hole_scale = (rcx - lcx) / HOLE_DISTANCE
                x = ((lcx - (LEFT_LOCATOR[0] + LEFT_HOLE_CENTRE) * hole_scale)
                     + (rcx - (RIGHT_LOCATOR[0] + RIGHT_HOLE_CENTRE) * hole_scale)) / 2.0
                y = ((lcy - (LEFT_LOCATOR[1] + LEFT_HOLE_CENTRE) * hole_scale)
                     + (rcy - (RIGHT_LOCATOR[1] + RIGHT_HOLE_CENTRE) * hole_scale)) / 2.0
                pairs.append((abs((rx - lx) - expected_dx) + abs(ry - ly), x, y, hole_scale))
    if not pairs:
        return None
    _, x, y, scale = min(pairs, key=lambda item: item[0])
    return x, y, scale


def _median_patch(frame, x, y, radius):
    cx, cy = int(round(x)), int(round(y))
    half = max(1, int(round(radius)))
    patch = frame[max(0, cy - half):min(frame.shape[0], cy + half + 1),
                  max(0, cx - half):min(frame.shape[1], cx + half + 1)]
    if patch.size == 0:
        return None
    return np.median(patch.reshape(-1, 3), axis=0)


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
    # Four interior corner patches avoid the centered status text and cell border.
    points = ((x + 6 * scale, y + 5 * scale), (x + 28 * scale, y + 5 * scale),
             (x + 6 * scale, y + 21 * scale), (x + 28 * scale, y + 21 * scale))
    votes = []
    for px, py in points:
        sample = _median_patch(frame, px, py, max(1, scale * 0.7))
        if sample is None:
            continue
        distances = sorted((float(np.linalg.norm(sample - np.asarray(color))), name)
                           for name, color in STATUS_COLORS.items())
        if distances[0][0] <= COLOR_TOLERANCE:
            votes.append(distances[0][1])
        elif float(np.linalg.norm(sample)) <= 35:
            votes.append("OUTSIDE")
        else:
            votes.append("UNKNOWN")
    if len(votes) < 3:
        return None
    counts = {}
    for value in votes:
        counts[value] = counts.get(value, 0) + 1
    winner, count = max(counts.items(), key=lambda item: item[1])
    return winner if count >= 3 else None


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


