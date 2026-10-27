"""Connection failure row, Retry row, ✕ icon, banner and shared blink, drawn by Tk (issue #134, spec v5.2 §4, §14.1, §14.3, §14.4).

Red row types and the tests that cover them (issue #144, "a test for every red row type"):
  Frame frozen         tests/test_monitor_hmi_144.py, test_monitor_hmi_128.py (icon), this file (all four at once)
  Heartbeat stopped    tests/test_monitor_hmi_128.py, test_monitor_hmi_144.py, this file (all four at once)
  Heartbeat not found  tests/test_monitor_hmi_130.py, this file (all four at once)
  Connection failure   this file
"""
import gc
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
tk = pytest.importorskip("tkinter")
try:
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402
import ui_i18n  # noqa: E402

WHITE = "#ffffff"
RED = "#B71C1C"
DIM = "#6A0F0F"
GREEN = "#2E7D32"
ORANGE_BG = "#FFF3E0"
ORANGE = "#EF6C00"


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    app.client.start_status_poll = lambda callback: None
    app.wm_maxsize(10000, 10000)
    if ui_i18n.current_language() != "en":
        app.on_toggle_language()
    try:
        yield app
    finally:
        if ui_i18n.current_language() != "en":
            app.on_toggle_language()
        app._is_closing = True
        app._file_log.close()
        app.destroy()
        del app
        gc.collect()


def row_of(device="DFU-07", **extra):
    base = {"device": device, "station": "DFU", "status": "DISCONNECTED", "is_connected": False,
            "connect_fail_count": 0, "connect_failed": False, "retrying": False, "retry_in_sec": None,
            "retry_threshold": 3, "first_fail_at": None, "last_connect_error": None}
    base.update(extra)
    return base


def failed(device="DFU-07", **extra):
    return row_of(device, connect_fail_count=3, connect_failed=True, retry_in_sec=7,
                  first_fail_at=1_700_000_000.0, last_connect_error="E_TIMEOUT", **extra)


def feed(ui, *rows):
    ui._update_status_ui({"status": "ok", "stations": list(rows)})
    ui.update_idletasks()
    return [ui._station_widgets[r["device"]] for r in rows]


def lamp_item(widgets):
    return {k: widgets["online_lamp"].itemcget(widgets["lamp_dot"], k) for k in ("fill", "outline")}


def non_bar_widgets(widgets):
    return [widgets["card"]] + [c for c in widgets["card"].winfo_children() if c is not widgets["bar"]]


def icon_types(widgets):
    icon = widgets["icon"]
    return {icon.type(i) for i in icon.find_all()}, [icon.itemcget(i, "text") for i in icon.find_all()
                                                     if icon.type(i) == "text"]


def after_ids(ui):
    return set(ui.tk.splitlist(ui.tk.call("after", "info")))


def test_failure_row_is_red_with_its_words_and_hollow_offline_lamp(ui):
    (w,) = feed(ui, failed())
    assert w["card"].cget("bg") == RED
    assert w["status_label"].cget("text") == "Conn. failure"
    assert w["bar"].cget("bg") == WHITE
    item = lamp_item(w)
    assert (w["online"].cget("text"), item["fill"], item["outline"]) == ("Offline", "", WHITE)
    assert w["light"].is_blinking() is False


def test_failure_row_words_in_chinese(ui):
    (w,) = feed(ui, failed())
    ui.on_toggle_language()
    ui.update_idletasks()
    assert w["status_label"].cget("text") == "連線失敗異常"


def test_failure_icon_is_a_white_square_with_a_red_x_and_is_raised(ui):
    (w,) = feed(ui, failed())
    types, texts = icon_types(w)
    assert "rectangle" in types and texts == ["✕"]
    icon = w["icon"]
    assert any(icon.itemcget(i, "fill") == WHITE for i in icon.find_all() if icon.type(i) == "rectangle")
    assert any(icon.itemcget(i, "fill") == RED for i in icon.find_all() if icon.type(i) == "text")
    stack = [str(c) for c in w["card"].tk.call("winfo", "children", str(w["card"]))]
    assert stack.index(str(icon)) > stack.index(str(w["light"]))  # the icon is above the light


def test_two_failure_rows_blink_in_sync_from_one_job(ui):
    a, b = feed(ui, failed("DFU-07"), failed("DFU-08"))
    job = ui._row_blink_job
    assert job is not None and job in after_ids(ui)
    assert ui._row_blink_lit is True
    for widget in non_bar_widgets(a) + non_bar_widgets(b):
        assert widget.cget("bg") == RED
    ui._row_blink_tick()
    assert ui._row_blink_lit is False
    for w in (a, b):
        for widget in non_bar_widgets(w):
            assert widget.cget("bg") == DIM
        assert w["bar"].cget("bg") == WHITE
        assert lamp_item(w)["fill"] == "" and lamp_item(w)["outline"] == WHITE
        assert w["status_label"].cget("fg") == WHITE
    assert ui._row_blink_job is not None and ui._row_blink_job != job  # one new job replaced the fired one
    ui._row_blink_tick()
    for w in (a, b):
        assert w["card"].cget("bg") == RED and lamp_item(w)["fill"] == ""


def test_polling_the_same_data_keeps_the_blink_phase(ui):
    a, = feed(ui, failed())
    ui._row_blink_tick()
    job = ui._row_blink_job
    feed(ui, failed())
    assert ui._row_blink_lit is False and ui._row_blink_job == job
    assert all(w.cget("bg") == DIM for w in non_bar_widgets(a))


def test_a_row_configured_mid_cycle_takes_the_current_phase(ui):
    a, b = feed(ui, failed("DFU-07"), row_of("DFU-08", status="IDLE", is_connected=True))
    ui._row_blink_tick()
    assert ui._row_blink_lit is False
    a, b = feed(ui, failed("DFU-07"), failed("DFU-08"))
    assert all(w.cget("bg") == DIM for w in non_bar_widgets(b))
    assert ui._row_blink_lit is False


def test_reconnect_returns_to_connected_and_stops_the_blink(ui):
    (w,) = feed(ui, failed())
    ui._row_blink_tick()
    assert "DFU-07" in ui._abnormal_devices()[0]
    (w,) = feed(ui, row_of(status="IDLE", is_connected=True, connect_fail_count=3, connect_failed=True))
    assert ui._row_blink_job is None
    assert w["card"].cget("bg") == "#E8F5E9" and w["status_label"].cget("text") == "Connected"
    assert all(c.cget("bg") == "#E8F5E9" for c in non_bar_widgets(w))
    assert ui._abnormal_devices() == []
    assert not ui.alarm_banner.winfo_manager()


def test_leaving_failure_in_the_dim_phase_shows_normal_colours_at_once(ui):
    (w,) = feed(ui, failed())
    ui._row_blink_tick()
    (w,) = feed(ui, row_of(connect_fail_count=1, retry_in_sec=10))
    assert w["card"].cget("bg") == ORANGE_BG
    assert ui._row_blink_job is None and ui._row_blink_lit is True


def test_no_failure_row_means_no_blink_job(ui):
    feed(ui, row_of(status="IDLE", is_connected=True), row_of("DFU-08", connect_fail_count=1, retry_in_sec=5))
    assert ui._row_blink_job is None


def test_core_down_turns_the_failure_row_to_offline_and_back(ui):
    (w,) = feed(ui, failed())
    assert ui._row_blink_job is not None
    ui._update_status_ui({"status": "error", "message": "down"})
    assert w["card"].cget("bg") == "#F5F5F5"
    assert w["status_label"].cget("text") == "Offline"
    assert ui._row_blink_job is None
    (w,) = feed(ui, failed())
    assert w["card"].cget("bg") == RED and ui._row_blink_job is not None


def test_banner_shows_the_connection_failure(ui):
    feed(ui, failed())
    assert ui.lbl_alarm_main.cget("text") == "■✕ DFU-07 Connection failure"
    assert ui.lbl_alarm_l2.cget("text") == ""
    icon = ui.alarm_icon
    assert [icon.itemcget(i, "text") for i in icon.find_all() if icon.type(i) == "text"] == ["✕"]
    assert any(icon.type(i) == "rectangle" for i in icon.find_all())


def test_banner_orders_connection_failure_before_heartbeat_before_frozen(ui):
    feed(ui, row_of("DFU-04", status="IDLE", is_connected=True, is_heartbeat_stopped=True),
         row_of("DFU-05", status="FROZEN", is_connected=True, is_frozen=True), failed("DFU-07"))
    assert ui.lbl_alarm_main.cget("text") == "3 devices abnormal"
    second = ui.lbl_alarm_l2.cget("text")
    assert second.index("■✕") < second.index("♥") < second.index("▲")
    assert [kind for _d, kind in ui._abnormal_devices()] == ["connect_failed", "heartbeat", "frozen"]


def test_retry_row_counts_down_each_second(ui):
    now = [100.0]
    ui._clock = lambda: now[0]
    (w,) = feed(ui, row_of(connect_fail_count=1, retry_in_sec=10))
    assert w["card"].cget("bg") == ORANGE_BG and w["status_label"].cget("fg") == ORANGE
    assert w["status_label"].cget("text") == "Retry in 10s (1/3)"
    assert ui._retry_countdown_job is not None
    for expected in (9, 8, 7):
        now[0] += 1
        ui._retry_countdown_tick()
        assert w["status_label"].cget("text") == f"Retry in {expected}s (1/3)"
    now[0] += 20
    ui._retry_countdown_tick()
    assert w["status_label"].cget("text") == "Retry in 0s (1/3)"
    (w,) = feed(ui, row_of(connect_fail_count=1, retry_in_sec=10))  # the next poll replaces it
    assert w["status_label"].cget("text") == "Retry in 10s (1/3)"
    assert ui._row_blink_job is None


def test_countdown_job_stops_when_no_retry_row_remains(ui):
    feed(ui, row_of(connect_fail_count=2, retry_in_sec=10))
    assert ui._retry_countdown_job is not None
    feed(ui, row_of(connect_fail_count=2, retrying=True))
    assert ui._retry_countdown_job is None
    (w,) = feed(ui, row_of(connect_fail_count=2, retrying=True))
    assert w["status_label"].cget("text") == "Retrying (3/3)"
    feed(ui, row_of(status="IDLE", is_connected=True))
    assert ui._retry_countdown_job is None


def test_row_tooltip_has_the_failure_lines_and_next_step(ui):
    import time
    (w,) = feed(ui, failed())
    text = w["row_tip"].current_text()
    lines = text.split("\n")
    kvm = next(i for i, line in enumerate(lines) if line.startswith("KVM:"))
    assert "Connection failures: 3 / 3" in lines[kvm + 1:]
    assert "First failure: " + time.strftime("%H:%M:%S", time.localtime(1_700_000_000.0)) in lines
    assert "Last error: E_TIMEOUT" in lines
    assert "Next retry in 7s" in lines
    assert lines[-1] == "Notify an engineer; first check JetKVM power and network cable" or \
        "Notify an engineer; first check JetKVM power and network cable" in lines
    assert w["tip"].current_text().startswith("Red, blinking: connection failure")


def test_retry_row_tooltip_has_no_next_step(ui):
    (w,) = feed(ui, row_of(connect_fail_count=1, retry_in_sec=10, first_fail_at=1_700_000_000.0,
                           last_connect_error="E1"))
    text = w["row_tip"].current_text()
    assert "Connection failures: 1 / 3" in text and "Next retry in 10s" in text
    assert "Notify an engineer" not in text
    assert w["tip"].current_text().startswith("Orange: connection failed")


def test_healthy_row_tooltip_is_unchanged(ui):
    (w,) = feed(ui, row_of(status="IDLE", is_connected=True))
    assert "Connection failures" not in w["row_tip"].current_text()


def test_row_geometry_does_not_change_for_failure_and_retry(ui):
    def geometry(w):
        return [(c.winfo_x(), c.winfo_y(), c.winfo_width(), c.winfo_height()) for c in w["card"].winfo_children()]
    (w,) = feed(ui, row_of(status="IDLE", is_connected=True))
    idle = geometry(w)
    feed(ui, failed())
    assert geometry(w) == idle
    feed(ui, row_of(connect_fail_count=1, retry_in_sec=3))
    assert geometry(w) == idle


def test_all_four_red_row_types_at_once(ui):
    rows = [
        row_of("DFU-01", status="FROZEN", is_connected=True, is_frozen=True),
        row_of("DFU-02", status="IDLE", is_connected=True, is_heartbeat_stopped=True),
        row_of("DFU-03", status="IDLE", is_connected=False, is_heartbeat_not_found=True),
        failed("DFU-04"),
    ]
    frozen, stopped, not_found, conn = feed(ui, *rows)
    for w in (frozen, stopped, not_found, conn):
        assert w["card"].cget("bg") in (RED, DIM)
        assert w["bar"].cget("bg") == WHITE
    assert [w["status_label"].cget("text") for w in (frozen, stopped, not_found, conn)] == [
        "Frame frozen", "Heartbeat stopped", "Heartbeat not found", "Conn. failure"]
    assert icon_types(frozen) == ({"polygon", "text"}, ["!"])
    assert icon_types(stopped) == ({"text"}, ["♥"])
    assert icon_types(not_found) == ({"text"}, ["♥"])
    assert icon_types(conn) == ({"rectangle", "text"}, ["✕"])
    assert (frozen["online"].cget("text"), lamp_item(frozen)["fill"]) == ("Online", GREEN)
    assert (stopped["online"].cget("text"), lamp_item(stopped)["fill"]) == ("Online", GREEN)
    assert (not_found["online"].cget("text"), lamp_item(not_found)["fill"]) == ("Offline", "")
    assert (conn["online"].cget("text"), lamp_item(conn)["fill"]) == ("Offline", "")
    assert lamp_item(conn)["outline"] == WHITE and lamp_item(not_found)["outline"] == WHITE
    assert ui._row_blink_job is not None
    ui._row_blink_tick()  # only the connection failure row blinks
    assert conn["card"].cget("bg") == DIM
    assert [w["card"].cget("bg") for w in (frozen, stopped, not_found)] == [RED, RED, RED]


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("extra", [
    dict(connect_fail_count=3, connect_failed=True),            # Conn. failure / 連線失敗異常
    dict(connect_fail_count=2, retry_in_sec=10),                # Retry in 10s (2/3) / 10 秒後重試（2/3）
    dict(connect_fail_count=2, retrying=True),                  # Retrying (3/3) / 重試中（3/3）
])
def test_new_row_words_fit_the_status_label(ui, lang, extra):
    import tkinter.font as tkfont
    (w,) = feed(ui, row_of(**extra))
    if lang == "zh":
        ui.on_toggle_language()
        ui.update_idletasks()
    label = w["status_label"]
    text = label.cget("text")
    width = label.winfo_width()
    font = tkfont.Font(font=label.cget("font"))
    if lang == "en" and not sys.platform.startswith("win"):  # measured on Windows CI with Microsoft JhengHei
        pytest.skip(f"the Windows row font is not used here ({font.actual('family')})")
    assert font.measure(text) <= width, (text, font.measure(text), width)
