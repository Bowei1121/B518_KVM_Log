"""Heartbeat not found on the row and in the banner, drawn by Tk (issue #130, spec v5.3 §14.1, §14.3, §14.4)."""
import gc
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import ui_i18n  # noqa: E402

WHITE = "#ffffff"
RED = "#B71C1C"
GREEN = "#2E7D32"
LABEL_W = 162


# ---------------------------------------------------------------------------------------------------------------
# Tk
# ---------------------------------------------------------------------------------------------------------------
tk = pytest.importorskip("tkinter")
try:
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import tkinter.font as tkfont  # noqa: E402

import ui_app  # noqa: E402

IDS = ["DFU-01", "DFU-02", "DFU-03", "FCT-01", "FCT-02", "BT-01"]


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


def show(ui, row):
    ui._update_status_ui({"status": "ok", "stations": [row]})
    ui.update_idletasks()
    return ui._station_widgets[row["device"]]


def row_of(**extra):
    return {"device": "DFU-01", "station": "DFU", "status": "IDLE", "is_connected": True, **extra}


def lamp_item(widgets):
    return {key: widgets["online_lamp"].itemcget(widgets["lamp_dot"], key) for key in ("fill", "outline", "width")}


def icon_texts_of(canvas):
    return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]


def test_not_found_row_is_red_with_its_words_and_lamp_follows_the_connection(ui):
    row = row_of(is_heartbeat_not_found=True)
    widgets = show(ui, row)
    assert widgets["card"].cget("bg") == RED
    assert widgets["status_label"].cget("text") == "Heartbeat not found"
    assert widgets["light"].is_blinking() is False
    assert (widgets["online"].cget("text"), lamp_item(widgets)["fill"]) == ("Online", GREEN)

    row.update(status="DISCONNECTED", is_connected=False)
    assert show(ui, row) is widgets
    assert widgets["status_label"].cget("text") == "Heartbeat not found"
    assert widgets["card"].cget("bg") == RED
    item = lamp_item(widgets)
    assert (widgets["online"].cget("text"), item["fill"], item["outline"], float(item["width"])) == (
        "Offline", "", WHITE, 3.0)

    row.update(status="IDLE", is_connected=True)
    show(ui, row)
    assert (widgets["online"].cget("text"), lamp_item(widgets)["fill"]) == ("Online", GREEN)


def test_not_found_row_words_in_chinese(ui):
    widgets = show(ui, row_of(is_heartbeat_not_found=True))
    ui.on_toggle_language()
    ui.update_idletasks()
    assert widgets["status_label"].cget("text") == "找不到心跳框"


def test_not_found_row_icon_is_a_white_heart(ui):
    row = row_of(is_heartbeat_not_found=True)
    widgets = show(ui, row)
    icon = widgets["icon"]
    assert icon_texts_of(icon) == ["♥"]
    assert icon.itemcget(icon.find_all()[0], "fill") == WHITE
    row.update(status="FROZEN", is_frozen=True)
    show(ui, row)
    assert widgets["status_label"].cget("text") == "Frame frozen"
    assert icon_texts_of(icon) == ["!"]


def geometry(widgets):
    return [(c.winfo_x(), c.winfo_y(), c.winfo_width(), c.winfo_height()) for c in widgets["card"].winfo_children()]


def test_no_child_moves_or_resizes_for_not_found(ui):
    widgets = show(ui, row_of())
    idle = geometry(widgets)
    show(ui, row_of(is_heartbeat_not_found=True))
    assert geometry(widgets) == idle
    label = widgets["status_label"]
    assert (label.winfo_x(), label.winfo_y(), label.winfo_width()) == (12, 29, LABEL_W)


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_not_found_words_fit_the_status_label(ui, lang):
    widgets = show(ui, row_of(is_heartbeat_not_found=True))
    if lang == "zh":
        ui.on_toggle_language()
        ui.update_idletasks()
    label = widgets["status_label"]
    text = label.cget("text")
    assert text == ("Heartbeat not found" if lang == "en" else "找不到心跳框")
    assert label.winfo_width() == LABEL_W
    font = tkfont.Font(font=label.cget("font"))
    if lang == "en" and not sys.platform.startswith("win"):  # measured on Windows CI with Microsoft JhengHei
        pytest.skip(f"the Windows row font is not used here ({font.actual('family')})")
    assert font.measure(text) <= LABEL_W


def show_rows(ui, extra):
    ui._update_status_ui({"status": "ok", "stations": [
        {"device": d, "station": d.split("-")[0], "status": "IDLE", "is_connected": True, **extra.get(d, {})}
        for d in IDS]})
    ui.geometry("960x1010")
    ui.update()


def labels(ui):
    return [ui.lbl_alarm_main.cget("text"), ui.lbl_alarm_l2.cget("text"), ui.lbl_alarm_l3.cget("text")]


def test_banner_single_not_found_device(ui):
    show_rows(ui, {"FCT-01": {"is_heartbeat_not_found": True}})
    assert labels(ui)[:2] == ["♥ FCT-01 Heartbeat not found", "Check Log Solution window is visible"]
    assert int(ui.alarm_icon.cget("width")) == ui_app.BANNER_ICON
    assert icon_texts_of(ui.alarm_icon) == ["♥"]
    ui.on_toggle_language()
    assert labels(ui)[:2] == ["♥ FCT-01 找不到心跳框", "請確認 Log Solution 視窗沒有被遮住"]


def test_banner_with_heartbeat_stopped_has_two_groups_and_one_heart(ui):
    show_rows(ui, {"FCT-01": {"is_heartbeat_not_found": True}, "DFU-02": {"is_heartbeat_stopped": True}})
    main, second, third = labels(ui)
    assert main == "2 devices abnormal"
    assert "Heartbeat stopped: DFU-02" in second and "Heartbeat not found: FCT-01" in second
    assert "Check test PC and Log Solution" in third and "Check Log Solution window is visible" in third
    assert int(ui.alarm_icon.cget("width")) == ui_app.BANNER_ICON
    assert icon_texts_of(ui.alarm_icon) == ["♥"]
    assert ui._banner_text_width(1) > ui._banner_text_width(2)


def test_banner_with_frozen_too_draws_heart_and_triangle(ui):
    show_rows(ui, {"FCT-01": {"is_heartbeat_not_found": True}, "DFU-02": {"is_heartbeat_stopped": True},
                   "DFU-03": {"status": "FROZEN", "is_frozen": True}})
    assert int(ui.alarm_icon.cget("width")) == 2 * ui_app.BANNER_ICON
    assert icon_texts_of(ui.alarm_icon) == ["♥", "!"]
    assert "polygon" in {ui.alarm_icon.type(i) for i in ui.alarm_icon.find_all()}


def test_banner_click_selects_in_category_order(ui):
    show_rows(ui, {"DFU-01": {"status": "FROZEN", "is_frozen": True}, "BT-01": {"is_heartbeat_not_found": True},
                   "FCT-02": {"is_heartbeat_stopped": True}})
    assert ui._abnormal_devices() == [("FCT-02", "heartbeat"), ("BT-01", "heartbeat_not_found"),
                                      ("DFU-01", "frozen")]
    ui.var_sel_device.set("DFU-02")
    ui.lbl_alarm_main.event_generate("<Button-1>", x=5, y=5)
    ui.update()
    assert ui.var_sel_device.get() == "FCT-02"
    show_rows(ui, {"DFU-01": {"status": "FROZEN", "is_frozen": True}, "BT-01": {"is_heartbeat_not_found": True}})
    ui.lbl_alarm_main.event_generate("<Button-1>", x=5, y=5)
    ui.update()
    assert ui.var_sel_device.get() == "BT-01"
