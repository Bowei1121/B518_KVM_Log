"""Heartbeat not found: presentation table, strings and banner text, Tk-free (issue #130, spec v5.3 §14.1, §14.4)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import alarm_banner  # noqa: E402
import ui_i18n  # noqa: E402
from alarm_banner import banner_lines  # noqa: E402
from station_presentation import present_station  # noqa: E402

GAP = "　"


@pytest.mark.parametrize("status", ["IDLE", "BUSY", "TESTING", "DISCONNECTED"])
@pytest.mark.parametrize("connected", [True, False])
def test_heartbeat_not_found_is_a_solid_red_row_in_the_banner(status, connected):
    """#130: is_heartbeat_not_found wins over status; red like Heartbeat stopped; the lamp follows is_connected."""
    res = present_station({"status": status, "is_heartbeat_not_found": True, "is_connected": connected})
    assert (res.text_key, res.status_key) == ("light_heartbeat_not_found", "station_heartbeat_not_found")
    assert (res.light, res.blink) == ("red", False)
    assert (res.row_bg, res.row_red, res.fg) == ("#B71C1C", True, "#ffffff")
    assert res.alarm_kind == "heartbeat_not_found"
    assert res.online == connected


def test_heartbeat_not_found_priority():
    base = {"status": "BUSY", "is_connected": True, "is_heartbeat_not_found": True}
    assert present_station({**base, "is_frozen": True}).alarm_kind == "frozen"
    assert present_station({**base, "is_heartbeat_stopped": True}).alarm_kind == "heartbeat"
    assert present_station({**base, "is_frozen": True, "is_heartbeat_stopped": True}).alarm_kind == "frozen"
    assert present_station(base).status_key == "station_heartbeat_not_found"  # red wins over BUSY


@pytest.mark.parametrize("status", ["IDLE", "BUSY", "DISCONNECTED"])
def test_heartbeat_not_found_false_changes_nothing(status):
    row = {"status": status, "is_connected": True}
    assert present_station({**row, "is_heartbeat_not_found": False}) == present_station(row)


def test_heartbeat_not_found_with_core_down_is_still_na():
    row = {"status": "IDLE", "is_heartbeat_not_found": True, "is_connected": True}
    assert present_station(row, core_connected=False) == present_station({"status": "DISCONNECTED"})


NOT_FOUND_TIP_EN = ("Red: heartbeat box not found. The Log Solution window may be covered, moved or minimized. "
                    "Bring it to the front so the heartbeat box is visible; the alarm clears once the heartbeat "
                    "is read again. If it stays red, notify an engineer.")
NOT_FOUND_TIP_ZH = ("紅燈：找不到心跳框。Log Solution 視窗可能被遮住、移走或縮小。請把它拉到最上層，讓心跳框露出來；"
                    "重新讀到心跳就會自動解除。還是紅色就請通知工程師。")


def always(_s):
    return True


def nf(*ids):
    return [(d, "heartbeat_not_found") for d in ids]


def test_strings_exist_in_en_and_zh():
    assert ui_i18n.t("station_heartbeat_not_found", "en") == "Heartbeat not found"
    assert ui_i18n.t("station_heartbeat_not_found", "zh") == "找不到心跳框"
    assert ui_i18n.t("alarm_hint_heartbeat_not_found", "en") == "Check Log Solution window is visible"
    assert ui_i18n.t("alarm_hint_heartbeat_not_found", "zh") == "請確認 Log Solution 視窗沒有被遮住"
    assert ui_i18n.t("light_heartbeat_not_found", "en") == NOT_FOUND_TIP_EN
    assert ui_i18n.t("light_heartbeat_not_found", "zh") == NOT_FOUND_TIP_ZH


def test_there_is_no_gray_no_heartbeat_check_string():
    for lang, table in ui_i18n.STRINGS.items():
        for key, value in table.items():
            assert "no heartbeat check" not in key.lower() and "no_heartbeat_check" not in key.lower(), (lang, key)
            assert "no heartbeat check" not in value.lower(), (lang, key)


@pytest.mark.parametrize("lang,expected", [
    ("en", ("♥ FCT-01 Heartbeat not found", "Check Log Solution window is visible", "")),
    ("zh", ("♥ FCT-01 找不到心跳框", "請確認 Log Solution 視窗沒有被遮住", "")),
])
def test_one_not_found_device_main_line_and_hint(lang, expected):
    ui_i18n.set_language(lang)
    try:
        assert banner_lines(nf("FCT-01"), [], [], always, always) == expected
    finally:
        ui_i18n.set_language("en")


@pytest.mark.parametrize("lang,expected", [
    ("en", ("3 devices abnormal",
            "♥ Heartbeat stopped: DFU-04" + GAP + "♥ Heartbeat not found: FCT-01" + GAP + "▲ Frame frozen: DFU-05",
            "♥ Check test PC and Log Solution" + GAP + "♥ Check Log Solution window is visible"
            + GAP + "▲ Check JetKVM")),
    ("zh", ("3 台設備異常",
            "♥ 心跳停止：DFU-04" + GAP + "♥ 找不到心跳框：FCT-01" + GAP + "▲ 畫面凍結：DFU-05",
            "♥ 請檢查測試電腦和 Log Solution" + GAP + "♥ 請確認 Log Solution 視窗沒有被遮住"
            + GAP + "▲ 請檢查 JetKVM")),
])
def test_three_categories_are_grouped_in_category_order(lang, expected):
    ui_i18n.set_language(lang)
    try:
        mixed = [("DFU-05", "frozen")] + nf("FCT-01") + [("DFU-04", "heartbeat")]
        assert banner_lines(mixed, [], [], always, always) == expected
    finally:
        ui_i18n.set_language("en")


def test_hint_line_lists_only_the_categories_present():
    ui_i18n.set_language("en")
    lines = banner_lines(nf("FCT-01", "FCT-02"), [], [], always, always)
    assert lines[0] == "2 devices abnormal"
    assert lines[1] == "♥ Heartbeat not found: FCT-01, FCT-02"
    assert lines[2] == "♥ Check Log Solution window is visible"


def test_category_order_and_shared_shape():
    mixed = [("A", "frozen"), ("B", "heartbeat_not_found"), ("C", "heartbeat"), ("D", "heartbeat_not_found")]
    assert alarm_banner.sort_abnormal(mixed) == [("C", "heartbeat"), ("B", "heartbeat_not_found"),
                                                 ("D", "heartbeat_not_found"), ("A", "frozen")]
    assert alarm_banner.shapes(["heartbeat", "heartbeat_not_found"]) == ["♥"]
    assert alarm_banner.shapes(["frozen", "heartbeat_not_found", "heartbeat"]) == ["♥", "▲"]
