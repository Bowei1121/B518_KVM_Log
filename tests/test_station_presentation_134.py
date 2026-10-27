"""Connection failure / Retry presentation, strings and banner text, Tk-free (issue #134, spec v5.2 §3, §4, §14.1, §14.3, §14.4)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import alarm_banner  # noqa: E402
import ui_i18n  # noqa: E402
from alarm_banner import banner_lines  # noqa: E402
from station_presentation import present_station  # noqa: E402
from ui_widgets import LED_COLORS  # noqa: E402

GAP = "　"


def always(_s):
    return True


def down(**extra):
    """A device that is not connected, with the Core connection-failure fields."""
    return {"status": "DISCONNECTED", "is_connected": False, "connect_fail_count": 0, "connect_failed": False,
            "retrying": False, "retry_in_sec": None, "retry_threshold": 3, **extra}


def test_count_zero_is_the_grey_offline_row():
    res = present_station(down())
    assert (res.light, res.row_red, res.in_banner, res.online) == ("grey", False, False, False)
    assert (res.status_key, res.row_bg, res.alarm_kind, res.row_blink) == ("station_offline", "#F5F5F5", "", False)


@pytest.mark.parametrize("count", [1, 2])
def test_waiting_retry_row_is_orange_and_not_an_alarm(count):
    res = present_station(down(connect_fail_count=count, retry_in_sec=10))
    assert (res.row_bg, res.fg, res.light, res.blink) == ("#FFF3E0", "#EF6C00", "orange", False)
    assert (res.row_red, res.in_banner, res.alarm_kind, res.online, res.row_blink) == (False, False, "", False, False)
    assert (res.text_key, res.status_key) == ("light_retry", "station_retry_in")
    assert ui_i18n.station_status_text(res, 10) == f"Retry in 10s ({count}/3)"


def test_retrying_row_shows_the_attempt_in_progress():
    res = present_station(down(connect_fail_count=1, retrying=True))
    assert res.status_key == "station_retrying" and not res.row_red
    assert ui_i18n.station_status_text(res) == "Retrying (2/3)"


def test_retry_total_comes_from_the_threshold_and_defaults_to_three():
    res = present_station(down(connect_fail_count=1, retry_threshold=5))
    assert ui_i18n.station_status_text(res, 4) == "Retry in 4s (1/5)"
    row = down(connect_fail_count=1)
    del row["retry_threshold"]
    assert ui_i18n.station_status_text(present_station(row), 4) == "Retry in 4s (1/3)"


def test_orange_is_a_light_colour():
    assert LED_COLORS["orange"] == "#EF6C00"


def test_connection_failure_row():
    res = present_station(down(connect_fail_count=3, connect_failed=True, retry_in_sec=7))
    assert (res.row_red, res.row_bg, res.fg, res.light, res.blink) == (True, "#B71C1C", "#ffffff", "red", False)
    assert (res.text_key, res.status_key, res.alarm_kind) == ("light_connect_failed", "station_connect_failed",
                                                              "connect_failed")
    assert (res.online, res.row_blink) == (False, True)


def test_only_the_failure_row_blinks():
    for row in ({"status": "IDLE", "is_connected": True}, {"status": "BUSY", "is_connected": True},
                {"status": "FROZEN", "is_frozen": True}, down(), down(connect_fail_count=1)):
        assert present_station(row).row_blink is False


def test_connected_row_ignores_stale_failure_fields():
    row = {"status": "IDLE", "is_connected": True, "connect_fail_count": 3, "connect_failed": True, "retrying": True}
    assert present_station(row) == present_station({"status": "IDLE", "is_connected": True})
    assert present_station(row).alarm_kind == ""


@pytest.mark.parametrize("field, kind", [("is_frozen", "frozen"), ("is_heartbeat_stopped", "heartbeat"),
                                         ("is_heartbeat_not_found", "heartbeat_not_found")])
def test_existing_red_state_survives_a_disconnect_and_failure(field, kind):
    res = present_station(down(connect_fail_count=3, connect_failed=True, **{field: True}))
    assert (res.alarm_kind, res.row_red, res.online, res.row_blink) == (kind, True, False, False)


def test_core_down_keeps_todays_disconnected_look():
    grey = present_station({"status": "DISCONNECTED"})
    for row in (down(connect_fail_count=3, connect_failed=True), down(connect_fail_count=1, retry_in_sec=5)):
        res = present_station(row, core_connected=False)
        assert res == grey and (res.row_red, res.row_blink, res.alarm_kind) == (False, False, "")


def test_strings_exact_in_both_languages():
    exact = {
        "station_connect_failed": ("Conn. failure", "連線失敗異常"),
        "alarm_connect_failed": ("Connection failure", "連線失敗異常"),
        "station_retry_in": ("Retry in {s}s ({n}/{total})", "{s} 秒後重試（{n}/{total}）"),
        "station_retrying": ("Retrying ({n}/{total})", "重試中（{n}/{total}）"),
        "row_tip_next_step": ("Notify an engineer; first check JetKVM power and network cable",
                              "請通知工程師；可先檢查 JetKVM 電源和網路線"),
        "station_offline": ("Offline", "未連線"),
    }
    for key, (en, zh) in exact.items():
        assert ui_i18n.t(key, "en") == en and ui_i18n.t(key, "zh") == zh, key
    assert ui_i18n.LANGUAGES == ("en", "zh")
    assert ui_i18n.t("light_connect_failed", "en").startswith("Red, blinking: connection failure")
    assert ui_i18n.t("light_connect_failed", "zh").startswith("紅色閃爍：連線失敗異常")
    assert ui_i18n.t("light_retry", "en").startswith("Orange: connection failed; Core retries automatically")
    assert ui_i18n.t("light_retry", "zh").startswith("橘燈：連線失敗，Core 會自動重試")
    assert ui_i18n.t("row_tip_conn_count", "en", n=2, total=3) == "Connection failures: 2 / 3"
    assert ui_i18n.t("row_tip_conn_count", "zh", n=2, total=3) == "連續失敗 2 / 3"
    assert ui_i18n.t("row_tip_first_fail", "en", time="09:05:01") == "First failure: 09:05:01"
    assert ui_i18n.t("row_tip_first_fail", "zh", time="09:05:01") == "首次失敗 09:05:01"
    assert ui_i18n.t("row_tip_last_error", "en", code="E1") == "Last error: E1"
    assert ui_i18n.t("row_tip_last_error", "zh", code="E1") == "最後錯誤 E1"
    assert ui_i18n.t("row_tip_next_retry", "en", s=4) == "Next retry in 4s"
    assert ui_i18n.t("row_tip_next_retry", "zh", s=4) == "下次重試 4 秒後"


def test_banner_single_device_has_no_hint_line():
    lines = banner_lines([("DFU-07", "connect_failed")], [], [], always, always)
    assert lines == ("■✕ DFU-07 Connection failure", "", "")


def test_banner_single_device_in_chinese():
    ui_i18n.set_language("zh")
    try:
        assert banner_lines([("DFU-07", "connect_failed")], [], [], always, always)[0] == "■✕ DFU-07 連線失敗異常"
    finally:
        ui_i18n.set_language("en")


def test_banner_category_order_and_hint_line():
    abnormal = [("DFU-05", "frozen"), ("DFU-04", "heartbeat"), ("DFU-07", "connect_failed")]
    main, second, third = banner_lines(abnormal, [], [], always, always)
    assert main == "3 devices abnormal"
    assert second == GAP.join(["■✕ Connection failure: DFU-07", "♥ Heartbeat stopped: DFU-04",
                               "▲ Frame frozen: DFU-05"])
    assert "■" not in third and "♥" in third and "▲" in third  # connection failure has no hint
    assert third.index("♥") < third.index("▲")
    assert alarm_banner.shapes(["frozen", "heartbeat", "connect_failed"]) == ["■✕", "♥", "▲"]
    assert [d for d, _k in alarm_banner.sort_abnormal(abnormal)] == ["DFU-07", "DFU-04", "DFU-05"]


def test_banner_two_connection_failures_have_an_empty_hint_line():
    main, second, third = banner_lines([("A-01", "connect_failed"), ("A-02", "connect_failed")], [], [], always, always)
    assert second == "■✕ Connection failure: A-01, A-02" or second.startswith("■✕ Connection failure: A-01")
    assert third == ""
