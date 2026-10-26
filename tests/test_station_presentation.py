# -*- coding: utf-8 -*-
"""Tests for station_presentation pure-Python lookup table (issue #120)."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

import ui_i18n  # noqa: E402
from station_presentation import (  # noqa: E402
    StationPresentation,
    present_station,
)
from ui_i18n import station_light_tip_key  # noqa: E402
from ui_widgets import WINDOW_BG, light_for_status  # noqa: E402


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            "IDLE",
            StationPresentation(
                text_key="light_idle",
                light="green",
                blink=False,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "OK",
            StationPresentation(
                text_key="light_idle",
                light="green",
                blink=False,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "BUSY",
            StationPresentation(
                text_key="light_busy",
                light="yellow",
                blink=False,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "TESTING",
            StationPresentation(
                text_key="light_busy",
                light="yellow",
                blink=False,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "FROZEN",
            StationPresentation(
                text_key="light_frozen",
                light="red",
                blink=True,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "DISCONNECTED",
            StationPresentation(
                text_key="light_disc",
                light="red",
                blink=False,
                row_bg="#f0f0f0",
                row_red=False,
                in_banner=False,
            ),
        ),
    ],
)
def test_exact_statuses_table_rows(status, expected):
    res = present_station({"status": status})
    assert res == expected
    assert res.text_key == expected.text_key
    assert res.light == expected.light
    assert res.blink == expected.blink
    assert res.row_bg == expected.row_bg
    assert res.row_red == expected.row_red
    assert res.in_banner == expected.in_banner


def test_station_presentation_is_frozen():
    pres = present_station({"status": "IDLE"})
    with pytest.raises((FrozenInstanceError, AttributeError)):
        pres.light = "yellow"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("status", "expected_key", "expected_light", "expected_blink"),
    [
        ("", "light_disc", "red", False),
        ("WEIRD", "light_disc", "red", False),
        (None, "light_disc", "red", False),
        ("idle", "light_idle", "green", False),
        ("  busy  ", "light_busy", "yellow", False),
        ("testing", "light_busy", "yellow", False),
        ("frozen", "light_frozen", "red", True),
        ("disconnected", "light_disc", "red", False),
        ("UNKNOWN_STATUS", "light_disc", "red", False),
        ("NOT_OK", "light_disc", "red", False),
        ("LOOKING", "light_disc", "red", False),
        ("IDLE_BUSY", "light_disc", "red", False),
        ("BUSY_FROZEN", "light_disc", "red", False),
        ("FROZEN?", "light_disc", "red", False),
    ],
)
def test_unknown_and_normalised_strings(status, expected_key, expected_light, expected_blink):
    res = present_station({"status": status})
    assert res.text_key == expected_key
    assert res.light == expected_light
    assert res.blink == expected_blink
    assert res.row_bg == "#f0f0f0"
    assert res.row_red is False
    assert res.in_banner is False


@pytest.mark.parametrize(
    "status",
    [
        "IDLE",
        "OK",
        "BUSY",
        "TESTING",
        "FROZEN",
        "DISCONNECTED",
        "WEIRD",
        "NOT_OK",
        "LOOKING",
        "IDLE_BUSY",
        "BUSY_FROZEN",
        "FROZEN?",
        "",
        None,
        "idle",
    ],
)
def test_core_disconnected_overrides_every_status(status):
    res = present_station({"status": status}, core_connected=False)
    disc = present_station({"status": "DISCONNECTED"}, core_connected=True)
    assert res == disc
    assert res.text_key == "light_disc"
    assert res.light == "red"
    assert res.blink is False
    assert res.row_bg == "#f0f0f0"
    assert res.row_red is False
    assert res.in_banner is False


@pytest.mark.parametrize(
    ("lang", "case", "expected_text"),
    [
        ('en', 'IDLE', 'Green: idle and ready to use.'),
        ('en', 'OK', 'Green: idle and ready to use.'),
        ('en', 'BUSY', 'Yellow: testing now.'),
        ('en', 'TESTING', 'Yellow: testing now.'),
        ('en', 'FROZEN', 'Blinking red: the picture is frozen (stuck).'),
        ('en', 'DISCONNECTED', 'Red: disconnected.'),
        ('en', 'WEIRD', 'Red: disconnected.'),
        ('en', 'CORE_DOWN', 'Red: disconnected.'),
        ('zh', 'IDLE', '綠燈：閒置，可以使用。'),
        ('zh', 'OK', '綠燈：閒置，可以使用。'),
        ('zh', 'BUSY', '黃燈：測試中。'),
        ('zh', 'TESTING', '黃燈：測試中。'),
        ('zh', 'FROZEN', '紅燈閃爍：畫面卡住不動。'),
        ('zh', 'DISCONNECTED', '紅燈：斷線。'),
        ('zh', 'WEIRD', '紅燈：斷線。'),
        ('zh', 'CORE_DOWN', '紅燈：斷線。'),
    ],
)
def test_hint_text_matches_literal_translation(lang, case, expected_text):
    pres = present_station(
        {"status": "IDLE" if case == "CORE_DOWN" else case},
        core_connected=case != "CORE_DOWN",
    )
    assert ui_i18n.t(pres.text_key, lang=lang) == expected_text


@pytest.mark.parametrize(
    "status",
    [
        "IDLE",
        "OK",
        "BUSY",
        "TESTING",
        "FROZEN",
        "DISCONNECTED",
        "WEIRD",
        "NOT_OK",
        "LOOKING",
        "IDLE_BUSY",
        "BUSY_FROZEN",
        "FROZEN?",
        "",
        None,
        "idle",
        "busy",
        "testing",
        "frozen",
    ],
)
def test_equivalence_with_light_for_status_and_station_light_tip_key(status):
    pres = present_station({"status": status})
    assert light_for_status(status) == (pres.light, pres.blink)
    assert station_light_tip_key(status) == pres.text_key


def test_row_background_is_todays_window_background():
    for status in ("IDLE", "BUSY", "FROZEN", "DISCONNECTED", "WEIRD"):
        assert present_station({"status": status}).row_bg == WINDOW_BG
        assert present_station({"status": status}, core_connected=False).row_bg == WINDOW_BG


@pytest.mark.parametrize("row", [None, {}])
def test_missing_row_or_status_is_disconnected(row):
    assert present_station(row) == present_station({"status": "DISCONNECTED"})


def test_extra_row_keys_are_ignored():
    row = {"status": "FROZEN", "device": "DFU-01", "other": "BUSY"}
    assert present_station(row) == present_station({"status": "FROZEN"})
