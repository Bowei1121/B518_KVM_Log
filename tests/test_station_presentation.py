# -*- coding: utf-8 -*-
"""Tests for station_presentation pure-Python lookup table (issue #120)."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

import ui_i18n  # noqa: E402
from station_presentation import StationPresentation, present_station  # noqa: E402
from ui_i18n import station_light_tip_key  # noqa: E402
from ui_widgets import light_for_status  # noqa: E402


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            "IDLE",
            StationPresentation(
                text_key="light_idle",
                light="green",
                blink=False,
                row_bg="#E8F5E9", status_key="station_connected", fg="#2E7D32", online=True,
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
                row_bg="#E8F5E9", status_key="station_connected", fg="#2E7D32", online=True,
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
                row_bg="#E3F2FD", status_key="station_busy", fg="#1565C0", online=True,
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
                row_bg="#E3F2FD", status_key="station_busy", fg="#1565C0", online=True,
                row_red=False,
                in_banner=False,
            ),
        ),
        (
            "FROZEN",
            StationPresentation(
                text_key="light_frozen",
                light="red",
                blink=False,
                row_bg="#B71C1C", status_key="station_frozen", fg="#ffffff", online=True,
                row_red=True,
                in_banner=False,
            ),
        ),
        (
            "DISCONNECTED",
            StationPresentation(
                text_key="light_disc",
                light="grey",
                blink=False,
                row_bg="#F5F5F5", status_key="station_offline", fg="#616161", online=False,
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
        ("", "light_disc", "grey", False),
        ("WEIRD", "light_disc", "grey", False),
        (None, "light_disc", "grey", False),
        ("idle", "light_idle", "green", False),
        ("  busy  ", "light_busy", "yellow", False),
        ("testing", "light_busy", "yellow", False),
        ("frozen", "light_frozen", "red", False),
        ("disconnected", "light_disc", "grey", False),
        ("UNKNOWN_STATUS", "light_disc", "grey", False),
        ("NOT_OK", "light_disc", "grey", False),
        ("LOOKING", "light_disc", "grey", False),
        ("IDLE_BUSY", "light_disc", "grey", False),
        ("BUSY_FROZEN", "light_disc", "grey", False),
        ("FROZEN?", "light_disc", "grey", False),
    ],
)
def test_unknown_and_normalised_strings(status, expected_key, expected_light, expected_blink):
    res = present_station({"status": status})
    assert res.text_key == expected_key
    assert res.light == expected_light
    assert res.blink == expected_blink
    assert res.row_bg == {"green": "#E8F5E9", "yellow": "#E3F2FD", "red": "#B71C1C", "grey": "#F5F5F5"}[res.light]
    assert res.row_red is (res.light == "red")
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
    assert res.light == "grey"
    assert res.blink is False
    assert res.row_bg == "#F5F5F5"
    assert res.row_red is False
    assert res.in_banner is False


@pytest.mark.parametrize(
    ("lang", "case", "expected_text"),
    [
        ('en', 'IDLE', 'Green: idle and ready to use.'),
        ('en', 'OK', 'Green: idle and ready to use.'),
        ('en', 'BUSY', 'Yellow: testing now.'),
        ('en', 'TESTING', 'Yellow: testing now.'),
        ('en', 'FROZEN', 'Red: the picture is frozen (stuck).'),
        ('en', 'DISCONNECTED', 'Grey: offline (not connected).'),
        ('en', 'WEIRD', 'Grey: offline (not connected).'),
        ('en', 'CORE_DOWN', 'Grey: offline (not connected).'),
        ('zh', 'IDLE', '綠燈：閒置，可以使用。'),
        ('zh', 'OK', '綠燈：閒置，可以使用。'),
        ('zh', 'BUSY', '黃燈：測試中。'),
        ('zh', 'TESTING', '黃燈：測試中。'),
        ('zh', 'FROZEN', '紅燈：畫面卡住不動。'),
        ('zh', 'DISCONNECTED', '灰燈：未連線。'),
        ('zh', 'WEIRD', '灰燈：未連線。'),
        ('zh', 'CORE_DOWN', '灰燈：未連線。'),
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


@pytest.mark.parametrize("status", ["BUSY", "DISCONNECTED"])
def test_frozen_flag_overrides_busy_and_disconnected(status):
    row = {"status": status, "is_frozen": True, "is_busy": True, "is_connected": False}
    pres = present_station(row)
    assert (pres.light, pres.blink, pres.row_bg, pres.row_red, pres.online) == ("red", False, "#B71C1C", True, False)
    assert present_station(row, core_connected=False) == present_station({"status": "DISCONNECTED"})


@pytest.mark.parametrize("row", [None, {}])
def test_missing_row_or_status_is_disconnected(row):
    assert present_station(row) == present_station({"status": "DISCONNECTED"})


def test_extra_row_keys_are_ignored():
    row = {"status": "FROZEN", "device": "DFU-01", "other": "BUSY"}
    assert present_station(row) == present_station({"status": "FROZEN"})


def test_frozen_row_with_kvm_disconnected_stays_frozen():
    """User decision 2026-10-26 (#144, option B): Core sends status FROZEN with is_connected false.

    The row keeps its Frame frozen words, red light and red background; only the lamp follows the real KVM state.
    """
    res = present_station({"status": "FROZEN", "is_frozen": True, "is_connected": False})
    assert (res.text_key, res.status_key) == ("light_frozen", "station_frozen")
    assert (res.light, res.blink) == ("red", False)
    assert (res.row_bg, res.row_red, res.fg) == ("#B71C1C", True, "#ffffff")
    assert res.online is False


@pytest.mark.parametrize("status", ["FROZEN", "BUSY", "IDLE", "DISCONNECTED"])
@pytest.mark.parametrize("connected", [False, True])
def test_red_row_lamp_follows_kvm_connection_and_words_stay(status, connected):
    """#144: on every red row the lamp is the current is_connected; the row words and colours do not change."""
    res = present_station({"status": status, "is_frozen": True, "is_connected": connected})
    frozen = present_station({"status": "FROZEN"})
    assert res.row_red
    assert (res.text_key, res.status_key, res.light, res.blink, res.row_bg, res.fg) == (
        frozen.text_key, frozen.status_key, frozen.light, frozen.blink, frozen.row_bg, frozen.fg)
    assert res.online is connected
