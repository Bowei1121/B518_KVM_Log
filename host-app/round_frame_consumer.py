"""Fail-closed JetKVM consumer for B518 Log Solution display contract 1.1."""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Optional, Tuple

import cv2
import numpy as np


CONTRACT_VERSION = "1.1"
LOCATOR_SIZE = 22
LEFT_LOCATOR = (82, 2, 3)
RIGHT_LOCATOR = (320, 2, 11)
LOCATOR_INSET_SIZE = 8
LOCATOR_DISTANCE = RIGHT_LOCATOR[0] - LEFT_LOCATOR[0]
MARKER_ORIGIN = (278, 0)
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
COLOR_TOLERANCE = 20
TERMINAL = frozenset(("PASS", "FAIL", "NOTEST", "TIMEOUT"))


class MarkerState(str, Enum):
    STANDBY = "standby"
    MONITORING = "monitoring"
    REVIEW = "review"
    COMPLETE = "complete"


PATTERNS = {
    MarkerState.STANDBY: (1, 0, 0, 1),
    MarkerState.MONITORING: (1, 1, 0, 0),
    MarkerState.REVIEW: (1, 0, 1, 0),
    MarkerState.COMPLETE: (0, 1, 1, 0),
}

# BGR values copied from App KVM_DISPLAY_CONTRACT / STATUS_COLOURS.
STATUS_COLORS = {
    "PASS": (0, 239, 0),
    "FAIL": (0, 0, 255),
    "TESTING": (0, 255, 255),
    "NOTEST": (240, 75, 240),
    "WAITING": (217, 217, 217),
    "COMPLETING": (255, 199, 130),
    "STOPPED": (191, 191, 191),
    "TIMEOUT": (0, 153, 255),
}


@dataclass(frozen=True)
class FrameObservation:
    reliable: bool
    state: Optional[MarkerState] = None
    capacity: int = 0
    statuses: Tuple[str, ...] = ()
    reason: str = ""
    scale: float = 0.0
    app_origin: Tuple[float, float] = ()

    @property
    def results_complete(self):
        return (self.reliable and self.state == MarkerState.COMPLETE
                and self.capacity > 0 and len(self.statuses) == self.capacity
                and all(status in TERMINAL for status in self.statuses))


@dataclass(frozen=True)
class TakeDecision:
    kind: str
    device_id: str
    state: Optional[MarkerState] = None
    capacity: int = 0
    results: Tuple[Tuple[int, str], ...] = ()
    reason: str = ""


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
            if (abs(iw - expected_size) <= max(3, scale * 0.20)
                    and abs(ih - expected_size) <= max(3, scale * 0.20)):
                for expected, target in ((LEFT_LOCATOR[2], left), (RIGHT_LOCATOR[2], right)):
                    offset = expected * scale
                    if (abs((ix - x) - offset) <= max(2, scale * 0.20)
                            and abs((iy - y) - offset) <= max(2, scale * 0.20)):
                        target.append((x, y, scale))
            child = hierarchy[child][0]
    return left, right


def _locate_app(frame):
    left, right = _locator_candidates(frame)
    pairs = []
    for lx, ly, ls in left:
        for rx, ry, rs in right:
            scale = (rx - lx) / float(LOCATOR_DISTANCE)
            expected_dx = LOCATOR_DISTANCE * scale
            tolerance = max(3.0, expected_dx * 0.035)
            if (abs((rx - lx) - expected_dx) <= tolerance
                    and abs(ry - ly) <= max(3.0, scale * 0.12)
                    and abs(ls - rs) <= scale * 0.08
                    and abs(((ls + rs) / 2.0) - scale) <= scale * 0.08):
                pairs.append((abs((rx - lx) - expected_dx) + abs(ry - ly),
                              (lx - LEFT_LOCATOR[0] * scale + rx - RIGHT_LOCATOR[0] * scale) / 2.0,
                              (ly - LEFT_LOCATOR[1] * scale + ry - RIGHT_LOCATOR[1] * scale) / 2.0,
                              scale))
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


def inspect_app_frame(frame, received_at, now=None, max_age=1.0):
    """Decode one BGR JetKVM frame using contract 1.1 geometry and color samples."""
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

    locator_points = ((LEFT_LOCATOR[0] + 7) * scale, (LEFT_LOCATOR[1] + 7) * scale,
                      (LEFT_LOCATOR[0] + 1) * scale, (LEFT_LOCATOR[1] + 1) * scale,
                      (RIGHT_LOCATOR[0] + 1) * scale, (RIGHT_LOCATOR[1] + 1) * scale,
                      (RIGHT_LOCATOR[0] + 15) * scale, (RIGHT_LOCATOR[1] + 15) * scale)
    locator_values = [_median_patch(frame, x0 + locator_points[i], y0 + locator_points[i + 1],
                                    max(1, scale * 0.8)) for i in range(0, 8, 2)]
    if (any(value is None for value in locator_values)
            or np.mean(locator_values[0]) < WHITE_MIN
            or np.mean(locator_values[1]) > BLACK_MAX
            or np.mean(locator_values[2]) > BLACK_MAX
            or np.mean(locator_values[3]) < WHITE_MIN):
        return FrameObservation(False, reason="locator_orientation_or_contrast_invalid",
                                scale=scale, app_origin=(x0, y0))

    bits = []
    for row in range(2):
        for column in range(2):
            px = x0 + (MARKER_ORIGIN[0] + MARKER_QUIET + column * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale
            py = y0 + (MARKER_ORIGIN[1] + MARKER_QUIET + row * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale
            bits.append(_marker_bit(_median_patch(frame, px, py, max(1, scale * 1.2))))
    if any(bit is None for bit in bits):
        return FrameObservation(False, reason="marker_low_contrast_or_occluded",
                                scale=scale, app_origin=(x0, y0))
    state = next((key for key, pattern in PATTERNS.items() if pattern == tuple(bits)), None)
    if state is None:
        return FrameObservation(False, reason="unknown_marker_pattern", scale=scale,
                                app_origin=(x0, y0))

    layout_bits = tuple(_marker_bit(_median_patch(
        frame, x0 + (LAYOUT_ORIGIN[0] + MARKER_QUIET
                      + column * (MARKER_CELL_SIZE + MARKER_GAP)
                      + MARKER_CELL_SIZE / 2.0) * scale,
        y0 + (LAYOUT_ORIGIN[1] + MARKER_QUIET + MARKER_CELL_SIZE / 2.0) * scale,
        max(1, scale * 1.2))) for column in range(2))
    rows = {(1, 0): 1, (0, 1): 2}.get(layout_bits)
    if rows is None:
        return FrameObservation(False, state, reason="layout_marker_missing_or_invalid",
                                scale=scale, app_origin=(x0, y0))
    required_height = y0 + (RESULT_FIRST_Y + (rows - 1) * RESULT_ROW_STEP
                            + RESULT_CELL_HEIGHT) * scale
    if required_height > frame.shape[0]:
        return FrameObservation(False, state, reason="app_band_cropped", scale=scale,
                                app_origin=(x0, y0))

    statuses = []
    outside_seen = False
    for slot in range(1, rows * RESULT_COLUMNS + 1):
        row, column = divmod(slot - 1, RESULT_COLUMNS)
        sx = x0 + column * RESULT_CELL_WIDTH * scale
        sy = y0 + (RESULT_FIRST_Y + row * RESULT_ROW_STEP) * scale
        status = _sample_status(frame, sx, sy, scale)
        if status is None or status == "UNKNOWN":
            return FrameObservation(False, state, reason="result_cell_unknown_{}".format(slot),
                                    scale=scale, app_origin=(x0, y0))
        if status == "OUTSIDE":
            outside_seen = True
            continue
        if outside_seen:
            return FrameObservation(False, state, reason="non_contiguous_capacity", scale=scale,
                                    app_origin=(x0, y0))
        statuses.append(status)
    if not statuses:
        return FrameObservation(False, state, reason="capacity_not_visible", scale=scale,
                                app_origin=(x0, y0))
    capacity = len(statuses)
    if rows == 2 and capacity <= RESULT_COLUMNS:
        return FrameObservation(False, state, reason="layout_capacity_mismatch",
                                scale=scale, app_origin=(x0, y0))
    return FrameObservation(True, state, capacity, tuple(statuses), scale=scale,
                            app_origin=(x0, y0))


class RoundFrameGate:
    """Require monitoring then a stable complete frame, and take each device round once."""

    def __init__(self, device_id, stable_complete_frames=2, max_age=1.0,
                 max_frame_gap=1.0, clock=time.monotonic,
                 require_presentation_time=False):
        self.device_id = str(device_id)
        self.stable_complete_frames = max(1, int(stable_complete_frames))
        self.max_age = float(max_age)
        self.max_frame_gap = float(max_frame_gap)
        self.clock = clock
        self.require_presentation_time = bool(require_presentation_time)
        self.armed = False
        self.taken = False
        self.last_sequence = None
        self.last_stream_id = None
        self.last_received_at = None
        self.last_presentation_time = None
        self.armed_capacity = None
        self._candidate_signature = None
        self._candidate_count = 0

    def observe(self, frame, sequence, received_at, now=None, stream_id=None,
                presentation_time=None):
        """Consume a fresh raw frame and return waiting/paused/taken/already_taken/unknown."""
        now = self.clock() if now is None else now
        if stream_id != self.last_stream_id:
            self.last_sequence = None
            self.last_stream_id = stream_id
            # A reconnect may show a retained Complete screen from a previous
            # process/session. Require a fresh Monitoring frame on this stream.
            self.armed = False
            self.taken = False
            self.last_presentation_time = None
            self.armed_capacity = None
            self._candidate_signature = None
            self._candidate_count = 0
        if self.last_sequence is not None and sequence <= self.last_sequence:
            return TakeDecision("waiting", self.device_id, reason="duplicate_or_out_of_order_frame")
        self.last_sequence = sequence
        if self.require_presentation_time and presentation_time is None:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("unknown", self.device_id, reason="missing_source_frame_time")
        if presentation_time is not None:
            if (self.last_presentation_time is not None
                    and presentation_time <= self.last_presentation_time):
                self._candidate_signature = None
                self._candidate_count = 0
                return TakeDecision("unknown", self.device_id,
                                    reason="out_of_order_source_frame")
            self.last_presentation_time = presentation_time
        previous_received_at = self.last_received_at
        self.last_received_at = received_at
        observation = inspect_app_frame(frame, received_at, now, self.max_age)
        if not observation.reliable:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("unknown", self.device_id, reason=observation.reason)

        if observation.state == MarkerState.REVIEW:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("paused", self.device_id, observation.state,
                                observation.capacity, reason="confirmation_required")
        if observation.state == MarkerState.STANDBY:
            self.armed = False
            self.armed_capacity = None
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="standby")
        if observation.state == MarkerState.MONITORING:
            self.armed = True
            self.taken = False
            self.armed_capacity = observation.capacity
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="monitoring")
        if self.taken:
            return TakeDecision("already_taken", self.device_id, observation.state,
                                observation.capacity, reason="round_already_taken")
        if not self.armed:
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="monitoring_not_observed")
        if observation.capacity != self.armed_capacity:
            self.armed = False
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="round_capacity_changed")
        if not observation.results_complete:
            self._candidate_signature = None
            self._candidate_count = 0
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="incomplete_or_nonterminal_results")

        signature = (observation.capacity, observation.statuses)
        gap = (None if previous_received_at is None else
               max(0.0, received_at - previous_received_at))
        if signature == self._candidate_signature and gap <= self.max_frame_gap:
            self._candidate_count += 1
        else:
            self._candidate_signature = signature
            self._candidate_count = 1
        if self._candidate_count < self.stable_complete_frames:
            return TakeDecision("waiting", self.device_id, observation.state,
                                observation.capacity, reason="confirming_complete_frame")
        self.taken = True
        self.armed = False
        rows = tuple((index, status) for index, status in enumerate(observation.statuses, 1))
        return TakeDecision("taken", self.device_id, observation.state,
                            observation.capacity, rows, "reliable_complete")


def observe_latest_round_frame(kvm, gate, now=None):
    """Pass the newest decoded BGR frame through the public consumer seam."""
    if hasattr(kvm, "latest_frame"):
        snapshot = kvm.latest_frame()
        if snapshot is None:
            return TakeDecision("unknown", gate.device_id, reason="no_frame")
        if len(snapshot) < 5:
            return TakeDecision("unknown", gate.device_id, reason="missing_source_frame_time")
        frame, sequence, received_at, stream_id, presentation_time = snapshot[:5]
    else:
        frame = getattr(kvm, "frame", None)
        sequence = getattr(kvm, "frame_sequence", 1)
        received_at = getattr(kvm, "frame_received_monotonic", now)
        stream_id = getattr(kvm, "stream_id", "controlled-frame")
        presentation_time = getattr(kvm, "frame_presentation_time", None)
    if frame is None:
        return TakeDecision("unknown", gate.device_id, reason="no_frame")
    return gate.observe(frame, sequence, received_at, now=now, stream_id=stream_id,
                        presentation_time=presentation_time)


def tcp_round_reply(decision):
    """Stable line protocol for the one-round check endpoint."""
    if decision.kind == "taken":
        body = ",".join("{}::{}".format(slot, status)
                        for slot, status in decision.results)
        return "action_done,{}\r\n".format(body)
    if decision.kind == "paused":
        return "action_paused,review\r\n"
    if decision.kind == "already_taken":
        return "action_waiting,already_taken\r\n"
    if decision.kind == "unknown":
        return "action_waiting,unknown,{}\r\n".format(decision.reason)
    return "action_waiting,{}\r\n".format(decision.reason or "monitoring")
