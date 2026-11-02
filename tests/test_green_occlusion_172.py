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
