"""Heartbeat stopped on the row and in the banner (issue #128, spec v5.3 §14.1, §14.3, §14.4)."""
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

import tkinter.font as tkfont  # noqa: E402

import ui_app  # noqa: E402
import ui_i18n  # noqa: E402

WHITE = "#ffffff"
RED = "#B71C1C"
GREEN = "#2E7D32"
LABEL_W = 162


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


def icon_items(widgets):
    icon = widgets["icon"]
    types = {icon.type(i) for i in icon.find_all()}
    texts = [icon.itemcget(i, "text") for i in icon.find_all() if icon.type(i) == "text"]
    return types, texts


def test_heartbeat_row_is_red_with_its_words_and_lamp_follows_the_connection(ui):
    row = row_of(is_heartbeat_stopped=True)
    widgets = show(ui, row)
    assert widgets["card"].cget("bg") == RED
    assert widgets["status_label"].cget("text") == "Heartbeat stopped"
    assert widgets["light"].is_blinking() is False
    assert (widgets["online"].cget("text"), lamp_item(widgets)["fill"]) == ("Online", GREEN)

    row.update(status="DISCONNECTED", is_connected=False)
    assert show(ui, row) is widgets
    assert widgets["status_label"].cget("text") == "Heartbeat stopped"
    assert widgets["card"].cget("bg") == RED
    item = lamp_item(widgets)
    assert (widgets["online"].cget("text"), item["fill"], item["outline"], float(item["width"])) == (
        "Offline", "", WHITE, 3.0)

    row.update(status="IDLE", is_connected=True)
    show(ui, row)
    assert (widgets["online"].cget("text"), lamp_item(widgets)["fill"]) == ("Online", GREEN)


def test_heartbeat_row_words_in_chinese(ui):
    widgets = show(ui, row_of(is_heartbeat_stopped=True))
    ui.on_toggle_language()
    ui.update_idletasks()
    assert widgets["status_label"].cget("text") == "心跳停止"


def test_row_icon_is_a_heart_for_heartbeat_and_a_triangle_for_frozen(ui):
    row = row_of(is_heartbeat_stopped=True)
    widgets = show(ui, row)
    assert icon_items(widgets) == ({"text"}, ["♥"])
    assert widgets["icon"].itemcget(widgets["icon"].find_all()[0], "fill") == WHITE
    row.update(status="FROZEN", is_frozen=True)  # both: Frame frozen wins
    show(ui, row)
    assert widgets["status_label"].cget("text") == "Frame frozen"
    types, texts = icon_items(widgets)
    assert "polygon" in types and texts == ["!"]
    row.pop("is_frozen")
    row.update(status="IDLE")
    show(ui, row)
    assert icon_items(widgets) == ({"text"}, ["♥"])


def geometry(widgets):
    return [(c.winfo_x(), c.winfo_y(), c.winfo_width(), c.winfo_height()) for c in widgets["card"].winfo_children()]


def test_no_child_moves_or_resizes_between_idle_heartbeat_and_frozen(ui):
    widgets = show(ui, row_of())
    idle = geometry(widgets)
    show(ui, row_of(is_heartbeat_stopped=True))
    assert geometry(widgets) == idle
    show(ui, row_of(status="FROZEN", is_frozen=True))
    assert geometry(widgets) == idle
    assert widgets["icon"].winfo_x() == 12 and widgets["icon"].winfo_y() == 7
    label = widgets["status_label"]
    assert (label.winfo_x(), label.winfo_y(), label.winfo_width()) == (12, 29, LABEL_W)


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_heartbeat_words_fit_the_status_label(ui, lang):
    widgets = show(ui, row_of(is_heartbeat_stopped=True))
    if lang == "zh":
        ui.on_toggle_language()
        ui.update_idletasks()
    label = widgets["status_label"]
    text = label.cget("text")
    assert text == ("Heartbeat stopped" if lang == "en" else "心跳停止")
    assert label.winfo_width() == LABEL_W
    assert tkfont.Font(font=label.cget("font")).measure(text) <= LABEL_W


def test_heartbeat_strings_exist_in_en_and_zh():
    assert ui_i18n.t("light_heartbeat", "en") == "Red: the heartbeat on the test PC screen stopped changing."
    assert ui_i18n.t("light_heartbeat", "zh") == "紅燈：測試電腦畫面上的心跳停止變化。"
    assert ui_i18n.t("station_heartbeat", "zh") == "心跳停止"
    assert ui_i18n.t("alarm_hint_heartbeat", "zh") == "請檢查測試電腦和 Log Solution"
