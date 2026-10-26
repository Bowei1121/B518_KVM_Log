"""Red rows: the small KVM lamp follows the real connection (issue #144, user decision 2026-10-26, option B).

Every red row keeps its red background and status words; only the Online/Offline lamp changes with is_connected.
On a red row an Offline lamp is a white 3 px hollow ring with no fill, also while the row flashes.
"""
import gc
import sys
import time
from dataclasses import replace
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
from station_presentation import present_station  # noqa: E402

GREEN = "#2E7D32"
WHITE = "#ffffff"
RED = "#B71C1C"


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


def lamp_item(widgets):
    lamp = widgets["online_lamp"]
    dot = widgets["lamp_dot"]
    return {key: lamp.itemcget(dot, key) for key in ("fill", "outline", "width")}


def show(ui, row):
    ui._update_status_ui({"status": "ok", "stations": [row]})
    ui.update_idletasks()
    return ui._station_widgets[row["device"]]


def assert_red_frozen(widgets):
    assert widgets["card"].cget("bg") == RED
    assert widgets["status_label"].cget("text") == "Frame frozen"
    assert widgets["light"].is_blinking() is False


def assert_offline_hollow(widgets):
    assert widgets["online"].cget("text") == "Offline"
    item = lamp_item(widgets)
    assert item["fill"] == ""  # hollow: no white, no grey fill
    assert item["outline"] == WHITE
    assert float(item["width"]) == 3.0


def assert_online_green(widgets):
    assert widgets["online"].cget("text") == "Online"
    item = lamp_item(widgets)
    assert item["fill"] == GREEN
    assert item["outline"] == WHITE


@pytest.mark.parametrize("status", ["FROZEN", "BUSY", "IDLE"])
def test_frozen_row_lamp_goes_offline_hollow_and_back_online(ui, status):
    row = {"device": "DFU-01", "station": "DFU", "status": status, "is_frozen": True, "is_connected": True}
    widgets = show(ui, row)
    assert_red_frozen(widgets)
    assert_online_green(widgets)

    row.update(status="FROZEN" if status == "FROZEN" else "DISCONNECTED", is_connected=False)
    assert show(ui, row) is widgets
    assert_red_frozen(widgets)
    assert_offline_hollow(widgets)

    row.update(status=status, is_connected=True)
    assert show(ui, row) is widgets
    assert_red_frozen(widgets)
    assert_online_green(widgets)


def test_frozen_offline_lamp_words_in_chinese(ui):
    row = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "is_frozen": True, "is_connected": False}
    widgets = show(ui, row)
    ui.on_toggle_language()
    ui.update_idletasks()
    assert widgets["status_label"].cget("text") == "畫面凍結"
    assert widgets["online"].cget("text") == "未連線"
    assert lamp_item(widgets)["fill"] == ""
    row["is_connected"] = True
    show(ui, row)
    assert widgets["online"].cget("text") == "已連線"
    assert lamp_item(widgets)["fill"] == GREEN


def test_red_offline_lamp_stays_hollow_while_the_row_flashes(ui):
    """The connection-failure row flashes (#134); its Offline lamp never fills in either phase."""
    row = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "is_frozen": True, "is_connected": False}
    widgets = show(ui, row)
    flashing = replace(present_station(row), blink=True)
    ui._configure_station_row(widgets, flashing)
    assert widgets["light"].is_blinking()
    fills = set()
    deadline = time.monotonic() + 1.3
    while time.monotonic() < deadline:
        ui.update()
        fills.add(lamp_item(widgets)["fill"])
        time.sleep(0.05)
    assert fills == {""}
    assert lamp_item(widgets)["outline"] == WHITE


def test_non_red_lamp_keeps_its_look(ui):
    row = {"device": "DFU-01", "station": "DFU", "status": "DISCONNECTED", "is_connected": False}
    widgets = show(ui, row)
    assert widgets["online"].cget("text") == "Offline"
    assert (lamp_item(widgets)["fill"], lamp_item(widgets)["outline"]) == (WHITE, "#9E9E9E")
    row.update(status="IDLE", is_connected=True)
    show(ui, row)
    assert (lamp_item(widgets)["fill"], lamp_item(widgets)["outline"]) == (GREEN, GREEN)


@pytest.mark.parametrize("connected,en,zh", [(False, "KVM: Disconnected", "KVM：已斷線"),
                                             (True, "KVM: Connected", "KVM：已連線")])
def test_row_tooltip_shows_kvm_connection(ui, connected, en, zh):
    row = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "is_frozen": True, "is_connected": connected}
    widgets = show(ui, row)
    lines = widgets["row_tip"].current_text().splitlines()
    assert lines[0] == "Station: DFU"
    assert en in lines
    ui.on_toggle_language()
    ui.update_idletasks()
    assert zh in widgets["row_tip"].current_text().splitlines()


def test_row_tooltip_kvm_line_follows_reconnect(ui):
    row = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "is_frozen": True, "is_connected": False}
    widgets = show(ui, row)
    assert "KVM: Disconnected" in widgets["row_tip"].current_text().splitlines()
    row["is_connected"] = True
    show(ui, row)
    assert "KVM: Connected" in widgets["row_tip"].current_text().splitlines()
