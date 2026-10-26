"""Monitor UI HMI rules from issue #105: light theme, status lights, short errors, daily Log files.

Tk tests skip when there is no display. Pure-Python tests (status mapping, daily file rules,
packaging text) always run.
"""

import datetime as dt
import gc
import queue
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

from ui_logfile import DailyLogFile, log_dir_for_template_root, prune_old_logs  # noqa: E402
from ui_widgets import ALL_BG, ALL_FG, is_dark_appearance, light_for_status  # noqa: E402

tk = pytest.importorskip("tkinter")


# ---------------------------------------------------------------- pure rules

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("IDLE", ("green", False)),
        ("OK", ("green", False)),
        ("BUSY", ("yellow", False)),
        ("TESTING", ("yellow", False)),
        ("FROZEN", ("red", False)),
        ("DISCONNECTED", ("grey", False)),
        ("", ("grey", False)),
    ],
)
def test_station_status_maps_to_one_light(status, expected):
    assert light_for_status(status) == expected


def test_daily_log_writes_one_file_per_day_and_switches_at_midnight(tmp_path):
    clock = {"now": dt.datetime(2026, 10, 21, 23, 59, 50)}
    log = DailyLogFile(tmp_path, "monitor_ui", clock=lambda: clock["now"])
    log.write("First line", "第一行")
    clock["now"] = dt.datetime(2026, 10, 22, 0, 0, 1)
    log.write("Second line", "第二行")
    log.close()

    day1 = (tmp_path / "monitor_ui_2026-10-21.log").read_text(encoding="utf-8")
    day2 = (tmp_path / "monitor_ui_2026-10-22.log").read_text(encoding="utf-8")
    assert day1 == "23:59:50 | First line | 第一行\n"
    assert day2 == "00:00:01 | Second line | 第二行\n"


def test_daily_log_writes_english_then_chinese_on_every_line(tmp_path):
    log = DailyLogFile(tmp_path, "monitor_ui", clock=lambda: dt.datetime(2026, 10, 21, 9, 0, 0))
    line = log.write("English text", "中文說明")
    log.close()
    assert line == "09:00:00 | English text | 中文說明"
    assert "English text | 中文說明" in (tmp_path / "monitor_ui_2026-10-21.log").read_text(encoding="utf-8")


def test_prune_keeps_thirty_days_and_only_touches_matching_names(tmp_path):
    today = dt.date(2026, 10, 21)
    keep = tmp_path / "monitor_ui_2026-09-22.log"   # 29 days old
    drop = tmp_path / "monitor_ui_2026-09-21.log"   # 30 days old
    other = tmp_path / "core_service_2026-09-01.log"  # different prefix
    notes = tmp_path / "notes.txt"
    for path in (keep, drop, other, notes):
        path.write_text("x", encoding="utf-8")

    removed = prune_old_logs(tmp_path, today, prefix="monitor_ui")

    assert removed == [drop]
    assert keep.exists() and other.exists() and notes.exists()
    assert not drop.exists()


def test_opening_a_new_day_file_prunes_old_ones(tmp_path):
    old = tmp_path / "monitor_ui_2026-09-01.log"
    old.write_text("old", encoding="utf-8")
    log = DailyLogFile(tmp_path, "monitor_ui", clock=lambda: dt.datetime(2026, 10, 21, 8, 0, 0))
    log.write("hello")
    log.close()
    assert not old.exists()


def test_log_folder_sits_beside_template_root(tmp_path):
    assert log_dir_for_template_root(tmp_path / "template") == tmp_path / "logs"


def test_prune_skips_impossible_dates_and_survives_them(tmp_path):
    bad = tmp_path / "monitor_ui_2026-02-30.log"  # not a real date: left alone, no crash
    bad.write_text("x", encoding="utf-8")
    assert prune_old_logs(tmp_path, dt.date(2026, 10, 21), prefix="monitor_ui") == []
    assert bad.exists()


def test_mac_bundle_pins_light_appearance():
    spec = (ROOT / "host-app" / "AtlasKVM.spec").read_text(encoding="utf-8")
    script = (ROOT / "host-app" / "build_mac.command").read_text(encoding="utf-8")
    assert "'NSRequiresAquaSystemAppearance': True" in spec
    assert "PlistBuddy" in script and "NSRequiresAquaSystemAppearance bool true" in script


# ---------------------------------------------------------------- Tk behaviour

try:
    # Kept alive on purpose: destroying a Tk root before AtlasUI() segfaults Tk 9 (repo .venv).
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402
from ui_widgets import LED_GREEN, LED_GREY, LED_RED, LED_RED_DIM, LED_YELLOW, StatusLight, Tooltip  # noqa: E402

_TK_CLASSES = {"Frame", "Labelframe", "Label", "Button", "Canvas", "Text", "Entry",
               "Listbox", "Checkbutton", "Scrollbar", "Toplevel"}


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


@pytest.fixture
def ui(monkeypatch):
    # The first poll would dial a real Core Service; keep station selection from doing so too.
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    # Enabled cropper, so error-line assertions do not depend on whether cv2/PIL are installed.
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    try:
        yield app
    finally:
        app._file_log.close()
        app.destroy()
        # Tk objects freed on a worker thread abort the process (macOS), so collect here, on the main thread.
        del app
        gc.collect()


def _cget(widget, option):
    try:
        return str(widget.cget(option))
    except tk.TclError:
        return ""


def test_every_tk_widget_has_an_explicit_light_colour(ui):
    checked = 0
    for widget in _walk(ui):
        if widget.winfo_class() not in _TK_CLASSES:
            continue
        checked += 1
        bg = _cget(widget, "bg")
        assert bg in ALL_BG, f"{widget} bg={bg!r} is not a light palette colour"
        fg = _cget(widget, "fg")
        if fg:
            assert fg in ALL_FG, f"{widget} fg={fg!r} is not a light palette colour"
    assert checked > 20


def test_template_buttons_are_merged_and_capture_button_is_gone(ui):
    texts = [_cget(w, "text") for w in _walk(ui) if w.winfo_class() == "Button"]
    assert "✂ Make template" in texts
    assert "📋 Match results" in texts
    assert not any("Capture" in t for t in texts)


def test_pattern_button_is_disabled_with_reason_when_cropper_missing(monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", None)
    monkeypatch.setattr(ui_app, "_PATTERN_IMPORT_ERROR", ImportError("No module named 'cv2'"))
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    try:
        assert _cget(app.btn_create_pattern, "state") == "disabled"
        full_log = app.log_text.get("1.0", "end")
        assert "No module named 'cv2'" in full_log
        app.on_create_pattern()  # direct call must not crash and must log
        assert "Cannot make templates" in app.log_text.get("1.0", "end")
    finally:
        app._file_log.close()
        app.destroy()


def test_error_line_is_red_and_short_hmi_text_jumps_to_it(ui):
    detail = "Check error: Communication error with Core Service: timed out"
    ui._display_check_result({"status": "error", "message": "Communication error with Core Service: timed out"})

    assert ui.lbl_check_result.cget("text") == "Check failed (see log)"
    assert "timed out" not in ui.lbl_check_result.cget("text")
    error_ranges = ui.log_text.tag_ranges("error")
    assert error_ranges, "error line must carry the red tag"
    line_text = ui.log_text.get(error_ranges[0], error_ranges[1])
    assert detail in line_text

    # The label's click target points at the same Log line.
    mark = next(m for m in ui.log_text.mark_names() if m.startswith("log_") and detail in
                ui.log_text.get(m, f"{m} lineend"))
    assert ui.log_text.index(mark) == ui.log_text.index(error_ranges[0])


def test_warning_line_is_orange_tag(ui):
    ui._append_log("No device selected; cannot take a snapshot.", "尚未選定設備，無法擷取快照。", level="warn")
    assert ui.log_text.tag_ranges("warn")
    assert not ui.log_text.tag_ranges("error")


def test_core_service_lamp_shows_detail_in_tip_and_logs_each_outage_once(ui):
    ui._update_status_ui({"status": "ok", "uptime_seconds": 49, "active_connections": 2, "busy_devices": []})
    assert ui.light_core.current_fill() == LED_GREEN
    assert "Running for 49 s" in ui._core_tooltip_text()
    assert "2 program(s)" in ui._core_tooltip_text()

    down = {"status": "error", "message": "Communication error with Core Service: timed out"}
    ui._update_status_ui(down)
    ui._update_status_ui(down)
    assert ui.light_core.current_fill() == LED_RED
    assert ui._core_tooltip_text() == "Service not connected (see log)"
    assert ui.log_text.get("1.0", "end").count("Core Service not connected") == 1


def test_frozen_station_is_solid_red_then_recovers(ui):
    frozen = {"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "FROZEN"}]}
    ui._update_status_ui(frozen)
    light = ui._station_widgets["DFU-01"]["light"]
    assert not light.is_blinking()
    assert light.current_fill() == LED_RED

    ui._update_status_ui({"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "IDLE"}]})
    assert not light.is_blinking()
    assert light.current_fill() == LED_GREEN


def test_repeated_blink_status_keeps_the_blink_phase(ui):
    # The status poll repeats FROZEN every 1.5 s; that must not restart the blink cycle.
    light = StatusLight(ui)
    light.set_light("red", blink=True)
    light._tick()  # dark half; the tick schedules the next timer
    job = light._job
    assert light.current_fill() == LED_RED_DIM
    light.set_light("red", blink=True)
    assert light.current_fill() == LED_RED_DIM
    assert light._job == job
    light._tick()
    assert light.current_fill() == LED_RED


def test_nine_stations_each_get_a_visible_grid_cell(ui):
    stations = [
        {"device": f"{name}-{n:02d}", "station": name, "status": "IDLE"}
        for name in ("DFU", "FCT", "BT") for n in (1, 2, 3)
    ]
    ui._update_status_ui({"status": "ok", "stations": stations})
    cells = set()
    for dev, widgets in ui._station_widgets.items():
        info = widgets["card"].grid_info()
        assert info, f"{dev} is not placed in the grid"
        cells.add((int(info["row"]), int(info["column"])))
    assert len(cells) == 9
    assert [(int(w["card"].grid_info()["row"]), int(w["card"].grid_info()["column"]))
            for w in ui._station_widgets.values()] == [(i, 0) for i in range(9)]


def test_more_than_three_cards_for_one_station_keep_device_list_order(ui):
    stations = [{"device": f"DFU-{n:02d}", "station": "DFU", "status": "IDLE"} for n in range(1, 5)]
    stations.append({"device": "FCT-01", "station": "FCT", "status": "IDLE"})
    ui._update_status_ui({"status": "ok", "stations": stations})
    cells = {dev: (int(w["card"].grid_info()["row"]), int(w["card"].grid_info()["column"]))
             for dev, w in ui._station_widgets.items()}
    assert len(set(cells.values())) == 5
    assert cells["DFU-03"] == (2, 0)
    assert cells["DFU-04"] == (3, 0)
    assert cells["FCT-01"] == (4, 0)


def test_restart_failure_is_short_on_the_settings_page_and_full_in_the_log(ui, monkeypatch):
    import core_daemon

    def fail(**_kwargs):
        return {"status": "error", "message": "Cannot start Core Daemon: Port 5000 is already in use."}

    monkeypatch.setattr(core_daemon, "restart_core_daemon", fail)
    res = ui._restart_core_daemon()
    assert res == {"status": "error", "message": "Restart failed (see log)"}
    # The worker thread queued the Log write; run it on the Tk side.
    while True:
        try:
            ui._ui_queue.get_nowait()()
        except queue.Empty:
            break
    assert "Port 5000 is already in use." in ui.log_text.get("1.0", "end")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS appearance control")
def test_every_window_is_light_even_in_dark_mode(ui, tmp_path, monkeypatch):
    import numpy as np
    import cv2

    import device_settings_window
    from pattern_tools import PatternCropper
    from stream_view import StreamerWindow
    from template_catalog import TemplateCatalog

    catalog = TemplateCatalog(tmp_path / "template")
    image_path = tmp_path / "capture.png"
    cv2.imwrite(str(image_path), np.zeros((120, 160, 3), dtype=np.uint8))

    # PhotoImage without a master uses Tk's default root; the app's first root is AtlasUI, so match that here.
    monkeypatch.setattr(tk, "_default_root", ui)
    windows = [
        ui_app.MatchResultsWindow(ui, catalog, "DFU"),
        device_settings_window.DeviceSettingsWindow(
            ui, client=None, path=tmp_path / "devices.json", restart_core=lambda: {"status": "ok"}
        ),
        StreamerWindow(ui, frame_getter=lambda: None),
        PatternCropper(ui, catalog, str(image_path), device="DFU", template_key="window"),
    ]
    try:
        ui.update_idletasks()  # maps the toplevels, as the mainloop would
        assert not is_dark_appearance(ui)
        for window in windows:
            assert not is_dark_appearance(window), window
    finally:
        for window in windows:
            window.destroy()


def test_busy_station_is_yellow_and_not_blinking(ui):
    ui._update_status_ui({"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "BUSY"}]})
    light = ui._station_widgets["DFU-01"]["light"]
    assert light.current_fill() == LED_YELLOW
    assert not light.is_blinking()


def test_status_light_stops_its_timer_when_not_blinking(ui):
    light = StatusLight(ui, bg="#f0f0f0")
    light.set_light("red", blink=True)
    assert light.is_blinking()
    light.set_light("red", blink=False)
    assert not light.is_blinking()


def test_tooltip_text_is_read_when_shown(ui):
    box = {"text": "first"}
    label = tk.Label(ui, text="hover")
    label.pack()
    tip = Tooltip(label, lambda: box["text"])
    box["text"] = "second"
    assert tip.current_text() == "second"
    tip.show()
    assert tip.is_visible()
    tip.hide()
    assert not tip.is_visible()


def test_snapshot_failure_shows_short_text_and_clears_when_fixed(ui):
    ui._display_snapshot_result({"status": "error", "message": "timed out after 5s"})
    assert ui.lbl_preview_info.cget("text") == "Snapshot failed (see log)"
    assert "timed out after 5s" in ui.log_text.get("1.0", "end")

    ui._set_info(ui.lbl_preview_info, "Size: 1x1", "#555555")
    assert ui.lbl_preview_info.cget("text") == "Size: 1x1"
    assert ui.lbl_preview_info.bind("<Button-1>") == ""


def test_core_disconnect_stops_all_station_blinking(ui):
    ui._update_status_ui({"status": "ok", "stations": [
        {"device": "DFU-01", "station": "DFU", "status": "FROZEN"},
        {"device": "FCT-01", "station": "FCT", "status": "IDLE"},
    ]})
    assert not ui._station_widgets["DFU-01"]["light"].is_blinking()
    ui._update_status_ui({"status": "error"})
    assert ui._station_widgets
    for w in ui._station_widgets.values():
        assert w["light"].current_fill() == LED_GREY
        assert w["light"].is_blinking() is False
        assert w["tip"].current_text() == "Grey: offline (not connected)."


@pytest.mark.parametrize("path", ["first_draw", "ok_poll", "disconnect"])
def test_station_presentation_receives_complete_core_row(ui, monkeypatch, path):
    first = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "extra_field": "x"}
    latest = {**first, "status": "BUSY", "extra_field": "updated"}
    calls = []
    real_present_station = ui_app.present_station

    def record(row, core_connected=True):
        calls.append((dict(row), core_connected))
        return real_present_station(row, core_connected=core_connected)

    if path != "first_draw":
        ui._update_status_ui({"status": "ok", "stations": [first]})
    if path == "disconnect":
        ui._update_status_ui({"status": "ok", "stations": [latest]})
    monkeypatch.setattr(ui_app, "present_station", record)

    if path == "first_draw":
        ui._stations = [first]
        ui._render_station_cards()
        assert calls == [(first, True)]
    elif path == "ok_poll":
        ui._update_status_ui({"status": "ok", "stations": [latest]})
        assert calls == [(latest, True)]
    else:
        ui._update_status_ui({"status": "error"})
        assert calls == [(latest, False)]


def test_station_tooltip_tracks_core_disconnect_and_recovery(ui):
    frozen = {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "extra_field": "x"}
    ui._update_status_ui({"status": "ok", "stations": [frozen]})
    w = ui._station_widgets["DFU-01"]
    assert w["tip"].current_text() == "Red: the picture is frozen (stuck)."

    ui._update_status_ui({"status": "error"})
    assert w["tip"].current_text() == "Grey: offline (not connected)."
    ui._update_status_ui({"status": "error"})
    assert w["tip"].current_text() == "Grey: offline (not connected)."

    ui._update_status_ui({"status": "ok", "stations": [{**frozen, "status": "IDLE"}]})
    assert w["tip"].current_text() == "Green: idle and ready to use."
