"""Monitor UI bilingual HMI rules from issue #106: English default, 🌐 switch, icons, tips, Log halves.

Tk tests skip when there is no display. Pure-Python tests (language tables, saved choice, icon rules) always run.
"""

import datetime as dt
import gc
import json
import re
import string
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

import ui_i18n  # noqa: E402
from ui_i18n import STRINGS, t, pair  # noqa: E402

tk = pytest.importorskip("tkinter")


# ---------------------------------------------------------------- pure rules

def _fields(text):
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_english_and_chinese_tables_have_the_same_keys():
    assert set(STRINGS["en"]) == set(STRINGS["zh"]), (
        set(STRINGS["en"]) ^ set(STRINGS["zh"])
    )


@pytest.mark.parametrize("key", sorted(STRINGS["en"]))
def test_every_key_is_translated_with_the_same_placeholders(key):
    en, zh = STRINGS["en"][key], STRINGS["zh"][key]
    assert en.strip() and zh.strip(), key
    assert _fields(en) == _fields(zh), key


def test_default_language_is_english_and_unknown_key_falls_back_to_key():
    assert ui_i18n.DEFAULT_LANGUAGE == "en"
    assert t("btn_check", lang="en") == "🩺 Check picture"
    assert t("no_such_key") == "no_such_key"


def test_pair_returns_english_then_chinese():
    assert pair("err_check") == ("Check failed", "診斷失敗")


def test_prefs_file_lives_in_the_user_settings_folder_per_os(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ui_i18n.prefs_path() == tmp_path / "Library" / "Application Support" / "AtlasKVM" / "ui_prefs.json"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert ui_i18n.prefs_path() == tmp_path / "Roaming" / "AtlasKVM" / "ui_prefs.json"


def test_saved_language_round_trips_and_bad_files_fall_back_to_english(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ui_i18n.load_saved_language() == "en"  # no file yet

    assert ui_i18n.save_language("zh") is True
    assert json.loads(ui_i18n.prefs_path().read_text(encoding="utf-8")) == {"language": "zh"}
    assert ui_i18n.load_saved_language() == "zh"

    ui_i18n.prefs_path().write_text("{not json", encoding="utf-8")
    assert ui_i18n.load_saved_language() == "en"
    ui_i18n.prefs_path().write_text(json.dumps({"language": "fr"}), encoding="utf-8")
    assert ui_i18n.load_saved_language() == "en"


def test_saving_the_language_never_raises_when_the_folder_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    blocker = tmp_path / "Library"
    blocker.write_text("a file, not a folder", encoding="utf-8")  # mkdir under it must fail
    assert ui_i18n.save_language("zh") is False


@pytest.mark.parametrize("name", ["ui_app.py", "ui_widgets.py", "ui_i18n.py", "device_settings_window.py",
                                  "pattern_tools.py", "stream_view.py", "device_list_editor.py"])
def test_no_vs16_emoji_in_the_hmi_sources(name):
    # U+FE0F makes ⚙️ / 👁️ render as boxes on Windows (issue #106); the icons must be the bare code points.
    source = (ROOT / "host-app" / name).read_text(encoding="utf-8")
    assert "️" not in source, name


def test_station_light_tip_matches_the_light_colour_rules():
    assert ui_i18n.station_light_tip_key("IDLE") == "light_idle"
    assert ui_i18n.station_light_tip_key("BUSY") == "light_busy"
    assert ui_i18n.station_light_tip_key("FROZEN") == "light_frozen"
    assert ui_i18n.station_light_tip_key("DISCONNECTED") == "light_disc"


# ---------------------------------------------------------------- Tk behaviour

try:
    # Kept alive on purpose: destroying a Tk root before AtlasUI() segfaults Tk 9 (repo .venv).
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    try:
        yield app
    finally:
        app._file_log.close()
        app.destroy()
        del app
        gc.collect()


def _buttons(ui):
    return [w for w in _walk(ui) if w.winfo_class() == "Button"]


def _find_button(ui, text):
    matches = [w for w in _buttons(ui) if str(w.cget("text")) == text]
    assert matches, f"no button with text {text!r}"
    return matches[0]


def test_starts_in_english_with_a_plain_title(ui):
    assert ui.title() == "Atlas2-518 Lightweight Monitor Client"
    assert ui_i18n.current_language() == "en"
    assert str(ui.btn_run_check.cget("text")) == "🩺 Check picture"
    assert str(ui.btn_create_pattern.cget("text")) == "✂ Make template"


def test_language_button_switches_every_open_window_live(ui, tmp_path):
    import device_settings_window

    settings = device_settings_window.DeviceSettingsWindow(ui, client=None, path=tmp_path / "devices.json")
    try:
        assert settings.title() == "Devices"
        _find_button(ui, "🌐").invoke()
        assert ui_i18n.current_language() == "zh"
        assert str(ui.btn_run_check.cget("text")) == "🩺 檢查畫面"
        assert settings.title() == "設備設定"
        assert str(ui.btn_create_pattern.cget("text")) == "✂ 製作模板"
    finally:
        settings.destroy()


def test_chosen_language_is_remembered_on_next_start(ui):
    ui.on_toggle_language()
    assert ui_i18n.current_language() == "zh"
    again = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    try:
        assert ui_i18n.current_language() == "zh"
        assert str(again.btn_run_check.cget("text")) == "🩺 檢查畫面"
    finally:
        again._file_log.close()
        again.destroy()
        del again
        gc.collect()


def test_icon_buttons_use_16pt_and_the_gear_is_larger(ui):
    def size_of(widget):
        return int(re.search(r"\b(\d+)\b", str(widget.cget("font"))).group(1))

    for icon in ("🔄", "📷", "🌐", "👁"):
        for widget in _buttons(ui):
            if str(widget.cget("text")) == icon:
                assert size_of(widget) == 16, icon
    gear = _find_button(ui, "⚙")
    assert size_of(gear) == 20


def test_every_button_and_input_has_a_tooltip(ui):
    missing = []
    for widget in _walk(ui):
        if widget.winfo_class() in ("Button", "Entry", "TCombobox") and not widget.bind("<Enter>"):
            missing.append(str(widget))
    assert missing == []


def test_tooltip_text_follows_the_language(ui):
    assert _find_button(ui, "🔄").bind("<Enter>")
    assert ui_i18n.t("tip_reconnect").startswith("Reconnect")
    ui.on_toggle_language()
    assert ui_i18n.t("tip_reconnect").startswith("重新連上")


def test_log_panel_shows_the_current_half_and_the_file_has_both(ui):
    ui._append_log("Service connected.", "服務已連線。")
    assert "Service connected." in ui.log_text.get("1.0", "end")
    assert "服務已連線" not in ui.log_text.get("1.0", "end")

    ui.on_toggle_language()
    ui._append_log("Service connected.", "服務已連線。")
    assert "服務已連線。" in ui.log_text.get("1.0", "end")

    ui._file_log.close()
    text = ui._file_log.path_for(dt.date.today()).read_text(encoding="utf-8")
    assert text.count(" | Service connected. | 服務已連線。\n") == 2


def test_warning_and_error_lines_keep_their_colour_in_both_languages(ui):
    ui._log_key("log_no_device_snapshot", level="warn")
    assert ui.log_text.tag_ranges("warn")
    ui.on_toggle_language()
    ui._log_key("log_snapshot_empty", level="error")
    assert ui.log_text.tag_ranges("error")


def test_check_failure_label_is_short_and_points_to_the_log(ui):
    ui._display_check_result({"status": "error", "message": "boom"})
    assert ui.lbl_check_result.cget("text") == "Check failed (see log)"
    ui.on_toggle_language()
    ui._display_check_result({"status": "error", "message": "boom"})
    assert ui.lbl_check_result.cget("text") == "診斷失敗（詳見日誌）"


def test_station_light_tooltip_and_metrics_follow_the_language(ui):
    ui._update_status_ui({"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "IDLE"}]})
    entry = ui._station_widgets["DFU-01"]
    assert str(entry["device"].cget("text")) == "Station: DFU"
    assert "Frame" in str(entry["metrics"].cget("text"))
    ui.on_toggle_language()
    assert str(entry["device"].cget("text")) == "站別：DFU"
    assert "影格" in str(entry["metrics"].cget("text"))


def test_match_results_read_both_note_shapes(ui, tmp_path):
    from match_diagnostics import MatchDiagnostics

    directory = MatchDiagnostics.latest_directory(ui._templates.root, "DFU")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps({
        "device": "DFU", "operation": "check", "ok": False,
        "records": [
            {"key": "frame", "score": None, "threshold": 0.8, "matched": False, "box": None,
             "note": "沒有 JetKVM 影格", "image": ""},
            {"key": "window", "score": None, "threshold": 0.8, "matched": False, "box": None,
             "note": {"en": "No JetKVM frame", "zh": "沒有 JetKVM 影格"}, "image": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    window = ui_app.MatchResultsWindow(ui, ui._templates, "DFU")
    try:
        window.listbox.selection_clear(0, "end")
        window.listbox.selection_set(0)
        window._select()
        assert window.image_label.cget("text") == "沒有 JetKVM 影格"  # old plain string still reads
        window.listbox.selection_clear(0, "end")
        window.listbox.selection_set(1)
        window._select()
        assert window.image_label.cget("text") == "No JetKVM frame"
        ui.on_toggle_language()
        window._select()
        assert window.image_label.cget("text") == "沒有 JetKVM 影格"
    finally:
        window.destroy()
