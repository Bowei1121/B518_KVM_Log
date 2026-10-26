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
from ui_widgets import LED_GREEN, TEXT_FG  # noqa: E402

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


def test_disconnected_status_with_kvm_connected_follows_the_flag():
    """#144 item 14 (b), user decision 2026-10-26 22:19: DISCONNECTED + is_connected=true is drawn as Connected."""
    res = present_station({"status": "DISCONNECTED", "is_connected": True})
    assert res == present_station({"status": "IDLE", "is_connected": True})
    assert (res.status_key, res.light, res.online) == ("station_connected", "green", True)


def test_disconnected_status_with_kvm_connected_row_reads_connected(ui):
    row = {"device": "DFU-01", "station": "DFU", "status": "DISCONNECTED", "is_connected": True}
    widgets = show(ui, row)
    assert widgets["status_label"].cget("text") == "Connected"
    assert widgets["status_label"].cget("fg") == GREEN
    assert widgets["light"].current_fill() == LED_GREEN
    assert widgets["online"].cget("text") == "Online"
    assert (lamp_item(widgets)["fill"], lamp_item(widgets)["outline"]) == (GREEN, GREEN)
    assert "KVM: Connected" in widgets["row_tip"].current_text().splitlines()
    ui.on_toggle_language()
    ui.update_idletasks()
    assert widgets["status_label"].cget("text") == "已連線"
    assert "KVM：已連線" in widgets["row_tip"].current_text().splitlines()


def na_item(widgets):
    lamp = widgets["online_lamp"]
    return {key: lamp.itemcget(widgets["lamp_dot"], key) for key in ("fill", "outline", "width", "dash")}


def assert_lamp_na(widgets, colour):
    item = na_item(widgets)
    assert item["fill"] == ""
    assert item["outline"] == colour
    assert float(item["width"]) == 3.0
    assert item["dash"] not in ("", "{}")  # dotted ring
    assert widgets["online"].cget("text") == "N/A"
    assert widgets["online"].cget("fg") == (WHITE if colour == WHITE else TEXT_FG)  # same as Online/Offline words
    assert widgets["online_lamp"].winfo_manager() == "place"  # never hidden


@pytest.mark.parametrize("status,connected", [("IDLE", True), ("DISCONNECTED", False), ("FROZEN", True), ("BUSY", True)])
def test_core_disconnected_lamp_is_na_and_recovers(ui, status, connected):
    """#144 item 14 (c), user decision 2026-10-26 22:19 (spec line 124, §14.5): Core down -> every lamp N/A."""
    row = {"device": "DFU-01", "station": "DFU", "status": status, "is_frozen": status == "FROZEN",
           "is_connected": connected}
    widgets = show(ui, row)
    ui._update_status_ui({"status": "error"})
    ui.update_idletasks()
    assert_lamp_na(widgets, "#9E9E9E")
    tip = widgets["row_tip"].current_text()
    assert "KVM: Unknown (Core disconnected)" in tip.splitlines()
    assert "Disconnected" not in tip.replace("Core disconnected", "")
    ui.on_toggle_language()
    ui.update_idletasks()
    assert widgets["online"].cget("text") == "未知"
    tip = widgets["row_tip"].current_text()
    assert "KVM：未知（Core 未連線）" in tip.splitlines()
    assert "已斷線" not in tip
    ui.on_toggle_language()
    show(ui, dict(row, status="IDLE", is_frozen=False, is_connected=True))
    assert widgets["online"].cget("text") == "Online"
    item = na_item(widgets)
    assert (item["fill"], item["outline"], item["dash"] in ("", "{}")) == (GREEN, GREEN, True)


def test_core_disconnected_lamp_on_red_last_status_row_is_white(ui):
    """Red Last status rows (#129) keep a red background during a Core outage; their N/A ring and words are white."""
    widgets = show(ui, {"device": "DFU-01", "station": "DFU", "status": "IDLE", "is_connected": True})
    ui._configure_station_row(widgets, present_station({"status": "FROZEN", "is_frozen": True}), core_connected=False)
    assert widgets["card"].cget("bg") == RED
    assert_lamp_na(widgets, WHITE)
