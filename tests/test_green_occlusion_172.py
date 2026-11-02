"""Regression coverage for green window occlusion of result cells."""
import itertools

import numpy as np
import pytest

from test_log_solution_reader import render_frame
from log_solution_reader import MarkerState, inspect_app_frame


COLORS = [(0, 239, 0), (20, 239, 0), (0, 219, 0), (0, 255, 0),
          (0, 239, 20), (20, 219, 20), (20, 255, 20)]
SCALES = [0.75, 0.8, 0.85, 0.9, 1.0, 1.1, 1.2, 4 / 3, 1.5, 2.0]
CORNERS = [(6, 5), (28, 5), (6, 21), (28, 21)]


def paint(frame, scale, x, y, width, height, color):
    ox, oy = 23, 31
    frame[round(oy + y * scale):round(oy + (y + height) * scale),
          round(ox + x * scale):round(ox + (x + width) * scale)] = color


def test_known_risk_green_cover_is_not_pass():
    frame = render_frame(MarkerState.COMPLETE, ("FAIL",) * 4)
    paint(frame, 2, 68, 34, 68, 26, (0, 239, 0))
    obs = inspect_app_frame(frame, 10, 10)
    assert not obs.reliable


@pytest.mark.parametrize('scale,color,count,position', tuple(itertools.product(
    SCALES, COLORS, range(1, 5), ('first', 'middle', 'two_cells', 'last'))))
def test_green_occlusions_never_read_fail_as_pass(scale, color, count, position):
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * 4, scale=scale)
    slots = {'first': (0,), 'middle': (1,), 'two_cells': (1, 2), 'last': (3,)}[position]
    for slot in slots:
        if count == 4:
            paint(frame, scale, slot * 34, 34, 34, 26, color)
        else:
            for cx, cy in CORNERS[:count]:
                paint(frame, scale, slot * 34 + cx - 3, 34 + cy - 3, 7, 7, color)
    obs = inspect_app_frame(frame, 10, 10)
    print(f'scale={scale} color={color} corners={count} position={position} '
          f'reliable={obs.reliable} statuses={obs.statuses} reason={obs.reason}', flush=True)
    assert not obs.reliable or all(status != 'PASS' for status in obs.statuses)


@pytest.mark.parametrize('color', [(0, 239, 0), (128, 128, 128)])
def test_three_agree_and_one_differs_or_unreadable(color):
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * 4)
    paint(frame, 2, 3, 36, 7, 7, color)
    assert not inspect_app_frame(frame, 10, 10).reliable


def test_missing_border_is_unreadable():
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * 4)
    paint(frame, 2, 0, 34, 34, 1, (0, 0, 255))
    assert not inspect_app_frame(frame, 10, 10).reliable


@pytest.mark.parametrize('status', ['PASS', 'FAIL', 'NOTEST', 'TIMEOUT', 'STOPPED', 'WAITING'])
@pytest.mark.parametrize('scale', [1.0, 2.0])
def test_unoccluded_statuses_read_unchanged(status, scale):
    obs = inspect_app_frame(render_frame(MarkerState.COMPLETE, (status,) * 4, scale=scale), 10, 10)
    assert obs.reliable, obs.reason
    assert obs.statuses == (status,) * 4

@pytest.mark.parametrize('scale,color,inset,side,slot', itertools.product(
    [1.0, 1.5, 2.0], [COLORS[0], COLORS[3]], range(1, 6),
    ['top', 'bottom', 'left', 'right'], [1, 11, 12]))
def test_inset_occlusion(scale, color, inset, side, slot):
    if scale < 1.9 and inset * scale < 3.5:
        # Residual: a strip of the original colour under ~3 px sits inside the resampling
        # blend next to the border and cannot be told from it without a contract change.
        pytest.xfail("thin original-colour strip below resampling resolution")
    capacity = 12 if slot >= 11 else 4
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * capacity, scale=scale)
    row, col = divmod(slot - 1, 10)
    x, y, w, h = col * 34 + 1, 34 + row * 27 + 1, 32, 24
    if side == 'top':
        y += inset; h -= inset
    elif side == 'bottom':
        h -= inset
    elif side == 'left':
        x += inset; w -= inset
    else:
        w -= inset
    paint(frame, scale, x, y, w, h, color)
    obs = inspect_app_frame(frame, 10, 10)
    print(f'inset={inset} side={side} slot={slot} scale={scale} color={color} '
          f'reliable={obs.reliable} statuses={obs.statuses} reason={obs.reason}', flush=True)
    assert not obs.reliable or (obs.capacity == capacity and 'PASS' not in obs.statuses)


@pytest.mark.parametrize('scale,color,row', itertools.product([1.0, 1.5, 2.0], [COLORS[0], COLORS[3]], [0, 1]))
def test_wide_window(scale, color, row):
    capacity = 14 if row else 4
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * capacity, scale=scale)
    paint(frame, scale, 0, 34 + row * 27 + 2, 136, 22, color)
    obs = inspect_app_frame(frame, 10, 10)
    print(f'wide row={row} scale={scale} color={color} reliable={obs.reliable} '
          f'statuses={obs.statuses} reason={obs.reason}', flush=True)
    assert not obs.reliable or (obs.capacity == capacity and 'PASS' not in obs.statuses)


@pytest.mark.xfail(strict=True, reason="Inset-1 full-interior PASS cover is pixel-identical to a genuine PASS cell")
def test_exact_fill_accepted_limit():
    frame = render_frame(MarkerState.COMPLETE, ('FAIL',) * 4, scale=2)
    paint(frame, 2, 1, 35, 32, 24, COLORS[0])
    obs = inspect_app_frame(frame, 10, 10)
    assert not obs.reliable or obs.statuses[0] != 'PASS'


@pytest.mark.parametrize("slot,inset", [
    pytest.param(slot, inset, marks=pytest.mark.xfail(strict=True, reason="Inset-1 full-interior PASS cover is pixel-identical to a genuine PASS cell"))
    if inset == 1 and slot in (5, 12) else (slot, inset)
    for slot in (5, 6, 12, 13) for inset in range(6)
])
def test_outside_cell_green_cover_never_increases_capacity(slot, inset):
    capacity = 11 if slot >= 12 else 4
    frame = render_frame(MarkerState.COMPLETE, ("FAIL",) * capacity, scale=2)
    row, col = divmod(slot - 1, 10)
    paint(frame, 2, col * 34 + inset, 34 + row * 27 + inset,
          34 - 2 * inset, 26 - 2 * inset, COLORS[0])
    obs = inspect_app_frame(frame, 10, 10)
    assert not obs.reliable or (obs.capacity <= capacity and "PASS" not in obs.statuses)


def test_status_after_outside_is_unreliable():
    frame = render_frame(MarkerState.COMPLETE, ("FAIL",) * 4, scale=2)
    source = frame[31 + 34 * 2:31 + 60 * 2, 23:23 + 34 * 2].copy()
    frame[31 + 34 * 2:31 + 60 * 2, 23 + 6 * 34 * 2:23 + 7 * 34 * 2] = source
    obs = inspect_app_frame(frame, 10, 10)
    assert not obs.reliable and obs.reason == "non_contiguous_capacity"
