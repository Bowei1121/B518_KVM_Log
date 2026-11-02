"""Core light drawn by Tk: symbol + words, not colour only; closing the bridge (#168)."""
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
from ui_widgets import LED_COLORS  # noqa: E402


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    app.client.start_status_poll = lambda callback: None
    app._core_status_path = tmp_path / "core_status.json"
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
        try:
            app.destroy()
        except tk.TclError:
            pass
        del app
        gc.collect()


def test_starting_before_the_first_poll_is_grey_with_symbol_and_words(ui):
    assert ui.lbl_core_state.cget("text") == "○ Core starting…"
    assert ui.light_core.current_fill() == LED_COLORS["grey"]


def test_connected_is_green_with_symbol_and_words(ui):
    ui._update_status_ui({"status": "ok", "uptime_seconds": 10, "stations": []})
    assert ui.lbl_core_state.cget("text") == "√ Core connected"
    assert ui.light_core.current_fill() == LED_COLORS["green"]
    assert ui.lbl_core_small.cget("text") == ""


def test_words_follow_the_language_switch(ui):
    ui._update_status_ui({"status": "ok", "uptime_seconds": 10, "stations": []})
    ui.on_toggle_language()
    assert ui.lbl_core_state.cget("text") == "√ Core 已連線"


def test_waiting_state_is_orange_triangle_and_tooltip_repeats_the_words(ui):
    ui._update_status_ui({"status": "ok", "uptime_seconds": 10, "stations": []})
    ui._clock = lambda: ui._core_ind.last_ok_at + 60
    ui._update_status_ui({"status": "error", "message": "refused"})
    assert ui.lbl_core_state.cget("text").startswith("△ Core stopped; restarting automatically")
    assert ui.light_core.current_fill() == LED_COLORS["orange"]
    assert ui._core_tooltip_text().startswith("Core stopped")


def test_closing_the_bridge_asks_nothing_and_sends_no_shutdown(ui, monkeypatch):
    sent = []
    monkeypatch.setattr(ui.client, "send_request", lambda cmd, *a, **k: sent.append(cmd) or {})
    for name in ("askyesno", "askokcancel", "askquestion", "askretrycancel"):
        monkeypatch.setattr(ui_app.messagebox, name, lambda *a, **k: pytest.fail("confirmation dialog shown"))
    ui._on_close()
    assert "shutdown" not in sent


def test_every_indicator_text_exists_in_english_and_chinese_with_exact_words():
    keys = [k for k in ui_i18n.STRINGS["en"] if k.startswith("core_ind_")]
    assert len(keys) == 14
    for lang in ("en", "zh"):
        assert all(k in ui_i18n.STRINGS[lang] for k in keys)
        assert "core_down" not in ui_i18n.STRINGS[lang] and "core_ok_tip" not in ui_i18n.STRINGS[lang]
    t = ui_i18n.t
    assert t("core_ind_starting", "zh") == "Core 啟動中…"
    assert t("core_ind_ok", "zh") == "Core 已連線"
    assert t("core_ind_restarted", "zh", time="08:02") == "Core 曾自動重啟 08:02"
    assert t("core_ind_waiting", "zh", minutes=1) == "Core 已停止，系統正在自動重開（約 1 分鐘）"
    assert t("core_ind_stopped_long", "zh", minutes=2) == "Core 停止超過 2 分鐘沒有恢復，請通知 TE 工程師"
    assert t("core_ind_invalid_host", "zh") == "Core 設定錯誤，無法啟動，請通知 TE 工程師"
    assert t("core_ind_invalid_host_small", "zh", path="LOG").startswith("設定的連線位址不是本機")
    assert "請通知 TE 工程師檢查設定檔" in t("core_ind_check_config", "zh", path="P")


# ---- bridge startup fallback ----------------------------------------------------------

