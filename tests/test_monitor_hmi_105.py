"""Monitor UI HMI rules from issue #105: light theme, status lights, short errors, daily Log files.

Tk tests skip when there is no display. Pure-Python tests (status mapping, daily file rules,
packaging text) always run.
"""

import datetime as dt
import gc
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

from ui_logfile import DailyLogFile, log_dir_for_template_root, prune_old_logs  # noqa: E402
from ui_widgets import ALL_BG, ALL_FG, light_for_status  # noqa: E402

tk = pytest.importorskip("tkinter")


# ---------------------------------------------------------------- pure rules

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("IDLE", ("green", False)),
        ("OK", ("green", False)),
        ("BUSY", ("yellow", False)),
        ("TESTING", ("yellow", False)),
        ("FROZEN", ("red", True)),
        ("DISCONNECTED", ("red", False)),
        ("", ("red", False)),
    ],
)
def test_station_status_maps_to_one_light(status, expected):
    assert light_for_status(status) == expected


def test_daily_log_writes_one_file_per_day_and_switches_at_midnight(tmp_path):
    clock = {"now": dt.datetime(2026, 10, 21, 23, 59, 50)}
    log = DailyLogFile(tmp_path, "monitor_ui", clock=lambda: clock["now"])
    log.write("第一行")
    clock["now"] = dt.datetime(2026, 10, 22, 0, 0, 1)
    log.write("第二行")
    log.close()

    day1 = (tmp_path / "monitor_ui_2026-10-21.log").read_text(encoding="utf-8")
    day2 = (tmp_path / "monitor_ui_2026-10-22.log").read_text(encoding="utf-8")
    assert day1 == "2026-10-21(23:59:50): 第一行\n"
    assert day2 == "2026-10-22(00:00:01): 第二行\n"


def test_daily_log_accepts_english_and_chinese_halves(tmp_path):
    log = DailyLogFile(tmp_path, "monitor_ui", clock=lambda: dt.datetime(2026, 10, 21, 9, 0, 0))
    line = log.write("中文說明", "English text")
    log.close()
    assert line.endswith(": 中文說明")
    assert "中文說明" in (tmp_path / "monitor_ui_2026-10-21.log").read_text(encoding="utf-8")


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


def test_mac_bundle_pins_light_appearance():
    spec = (ROOT / "host-app" / "AtlasKVM.spec").read_text(encoding="utf-8")
    script = (ROOT / "host-app" / "build_mac.command").read_text(encoding="utf-8")
    assert "'NSRequiresAquaSystemAppearance': True" in spec
    assert "PlistBuddy" in script and "NSRequiresAquaSystemAppearance bool true" in script


# ---------------------------------------------------------------- Tk behaviour

try:
    _probe = tk.Tk()
    _probe.destroy()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402
from ui_widgets import LED_GREEN, LED_RED, LED_RED_DIM, LED_YELLOW, StatusLight, Tooltip  # noqa: E402

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
    assert "✂️ 製作模板" in texts
    assert "📋 比對結果" in texts
    assert not any("儲存目前影格為 Capture" in t for t in texts)


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
        assert "無法製作模板" in app.log_text.get("1.0", "end")
    finally:
        app._file_log.close()
        app.destroy()


def test_error_line_is_red_and_short_hmi_text_jumps_to_it(ui):
    detail = "診斷錯誤: Communication error with Core Service: timed out"
    ui._display_check_result({"status": "error", "message": "Communication error with Core Service: timed out"})

    assert ui.lbl_check_result.cget("text") == "診斷失敗（詳見日誌）"
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
    ui._append_log("尚未選定設備，無法擷取快照。", level="warn")
    assert ui.log_text.tag_ranges("warn")
    assert not ui.log_text.tag_ranges("error")


def test_core_service_lamp_shows_detail_in_tip_and_logs_each_outage_once(ui):
    ui._update_status_ui({"status": "ok", "uptime_seconds": 49, "active_connections": 2, "busy_devices": []})
    assert ui.light_core.current_fill() == LED_GREEN
    assert "Uptime: 49s" in ui._core_tooltip_text()
    assert "連線數: 2" in ui._core_tooltip_text()

    down = {"status": "error", "message": "Communication error with Core Service: timed out"}
    ui._update_status_ui(down)
    ui._update_status_ui(down)
    assert ui.light_core.current_fill() == LED_RED
    assert ui._core_tooltip_text() == "服務未連線（詳見日誌）"
    assert ui.log_text.get("1.0", "end").count("Core Service 未連線") == 1


def test_frozen_station_blinks_then_stops_on_recovery(ui):
    frozen = {"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "FROZEN"}]}
    ui._update_status_ui(frozen)
    light = ui._station_widgets["DFU-01"]["light"]
    assert light.is_blinking()
    assert light.current_fill() == LED_RED

    light._tick()  # dark half of the cycle
    assert light.current_fill() == LED_RED_DIM
    light._tick()  # lit again
    assert light.current_fill() == LED_RED

    ui._update_status_ui({"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "IDLE"}]})
    assert not light.is_blinking()
    assert light.current_fill() == LED_GREEN


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
    assert ui.lbl_preview_info.cget("text") == "快照失敗（詳見日誌）"
    assert "timed out after 5s" in ui.log_text.get("1.0", "end")

    ui._set_info(ui.lbl_preview_info, "解析度: 1x1", "#555555")
    assert ui.lbl_preview_info.cget("text") == "解析度: 1x1"
    assert ui.lbl_preview_info.bind("<Button-1>") == ""
