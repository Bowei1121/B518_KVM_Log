"""Fixed station panel: geometry, presentation and interaction (issue #126)."""
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

import ui_app
from ui_widgets import ALL_BG, ALL_FG


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    # Do not dial a real Core when update() runs the initial timer.
    app.client.start_status_poll = lambda callback: None
    app.wm_maxsize(10000, 10000)
    try:
        yield app
    finally:
        app._is_closing = True
        app._file_log.close()
        app.destroy()
        del app
        gc.collect()


def rows(count):
    ids = [f"DFU-{i:02d}" for i in range(1, 11)] + ["FCT-01", "BT-01"]
    return [{"device": dev, "station": dev.split("-")[0], "status": "IDLE"} for dev in ids[:count]]


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


@pytest.mark.parametrize("count", [9, 12])
def test_twelve_fixed_cells_are_visible_without_scrolling(ui, count):
    devices = rows(count)
    ui._update_status_ui({"status": "ok", "stations": devices})
    ui.geometry("960x1010")
    ui.update()
    panel = ui.station_panel
    cells = ui.stations_container.winfo_children()
    assert panel.winfo_width() == 200
    assert len(cells) == 12
    assert ui.stations_container.winfo_reqheight() <= ui.station_canvas.winfo_height()
    for cell in cells:
        assert cell.winfo_viewable()
        assert (cell.winfo_width(), cell.winfo_height()) == (176, 56)
        assert cell.winfo_rooty() + cell.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()
    assert not any(w.winfo_class() in ("Scrollbar", "TScrollbar") for w in walk(panel))
    before = ui.station_canvas.yview()
    ui.station_canvas.yview_scroll(1, "units")
    assert ui.station_canvas.yview() == before
    assert list(ui._station_widgets) == [r["device"] for r in devices]
    for cell in cells[count:]:
        assert not cell.winfo_children()
        assert not cell.bind("<Button-1>")
    # The log belongs to the right column; the station panel reaches below it.
    assert ui.log_text.winfo_rootx() >= panel.winfo_rootx() + panel.winfo_width()
    assert panel.winfo_rooty() + panel.winfo_height() >= ui.log_text.winfo_rooty() + ui.log_text.winfo_height()


def geometry(widget):
    return widget.winfo_x(), widget.winfo_y(), widget.winfo_width(), widget.winfo_height()


def test_language_and_status_updates_keep_rows_and_children_fixed(ui):
    devices = rows(12)
    ui._update_status_ui({"status": "ok", "stations": devices})
    ui.geometry("960x1010")
    ui.update()
    widgets = [w for entry in ui._station_widgets.values() for w in walk(entry["card"])]
    before = {w: geometry(w) for w in widgets}
    for _ in range(2):
        ui.on_toggle_language()
        ui.update_idletasks()
        assert {w: geometry(w) for w in widgets} == before
    devices[0].update(status="BUSY", is_frozen=True, is_connected=False)
    ui._update_status_ui({"status": "ok", "stations": devices})
    ui.update_idletasks()
    assert {w: geometry(w) for w in widgets} == before
    row = ui._station_widgets["DFU-01"]
    assert row["card"].cget("bg") == "#B71C1C"
    assert not row["light"].is_blinking()
    for widget in walk(row["card"]):
        assert widget.cget("bg") in ALL_BG
        if widget.winfo_class() == "Label":
            assert widget.cget("fg") == "#ffffff"
            assert widget.cget("fg") in ALL_FG
    for widget in widgets:
        if widget is not row["card"] and widget.master is row["card"]:
            assert widget.winfo_manager() == "place"
    ui._update_status_ui({"status": "error"})
    assert row["card"].cget("bg") == "#F5F5F5"


def test_clicking_row_or_any_child_selects_device(ui):
    ui._update_status_ui({"status": "ok", "stations": rows(2)})
    ui.update_idletasks()
    for widget in walk(ui._station_widgets["DFU-02"]["card"]):
        ui.var_sel_device.set("DFU-01")
        widget.event_generate("<Button-1>")
        assert ui.var_sel_device.get() == "DFU-02"


# #144: an Offline lamp on a red row is a hollow white ring (no fill), so FROZEN offline expects fill "".
@pytest.mark.parametrize("status,connected,online,fill", [
    ("IDLE", None, True, "#2E7D32"), ("FROZEN", False, False, ""), ("DISCONNECTED", True, True, "#2E7D32")])
def test_online_lamp_uses_explicit_connection_flag(ui, status, connected, online, fill):
    row = {"device": "DFU-01", "station": "DFU", "status": status}
    if connected is not None:
        row["is_connected"] = connected
    ui._update_status_ui({"status": "ok", "stations": [row]})
    lamp = ui._station_widgets["DFU-01"]["online_lamp"]
    dot = lamp.find_all()[0]
    assert ui._station_widgets["DFU-01"]["online"].cget("text") == ("Online" if online else "Offline")
    assert lamp.itemcget(dot, "fill") == fill
    ui._update_status_ui({"status": "error"})
    assert lamp.itemcget(dot, "fill") == "#ffffff"


def test_frozen_device_stays_red_after_disconnect_and_recovers_in_place(ui):
    row = {"device": "DFU-01", "station": "DFU", "status": "BUSY",
           "is_busy": True, "is_frozen": True, "is_connected": True}
    ui._update_status_ui({"status": "ok", "stations": [row]})
    widgets = ui._station_widgets["DFU-01"]
    assert widgets["card"].cget("bg") == "#B71C1C"
    row.update(status="DISCONNECTED", is_busy=False, is_connected=False)
    ui._update_status_ui({"status": "ok", "stations": [row]})
    assert ui._station_widgets["DFU-01"] is widgets
    assert widgets["card"].cget("bg") == "#B71C1C"
    assert widgets["status_label"].cget("text") == "Frame frozen"
    assert widgets["online"].cget("text") == "Offline"
    assert not widgets["light"].is_blinking()
    row.update(status="IDLE", is_frozen=False, is_connected=True)
    ui._update_status_ui({"status": "ok", "stations": [row]})
    assert ui._station_widgets["DFU-01"] is widgets
    assert widgets["card"].cget("bg") == "#E8F5E9"
    assert widgets["status_label"].cget("text") == "Connected"
