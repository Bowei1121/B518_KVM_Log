"""Issue #181: Core 啟動失敗副文不含長路徑；路徑只在 tooltip／Log。"""
import datetime
import gc
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import core_indicator as ci
import ui_i18n

CONFIG = r"C:\Users\operator\Documents\template\devices.json"
LOG = r"C:\Users\operator\Documents\logs\core_service.log"


@pytest.mark.parametrize("reason", ["devices_missing", "devices_invalid", "port_in_use", "internal_error", "invalid_host"])
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_failure_text_and_tooltip(reason, lang):
    from ui_app import AtlasUI

    now = datetime.datetime.now().astimezone()
    indicator = ci.CoreIndicator(0, now, LOG)
    status = dict(state="failed", reason=reason, detail="bad", devices_file=CONFIG,
                  port=5000, updated_at=now.isoformat())
    view = indicator.observe(False, None, 1, now, status)
    small = ui_i18n.t(view.small_key, lang, **view.small_args)
    assert "\\" not in small and "Documents" not in small
    ui_i18n.set_language(lang)
    try:
        tip = AtlasUI._core_tooltip_text(SimpleNamespace(_core_view=view, _service_ok=False))
        assert CONFIG in tip and LOG in tip
        assert ui_i18n.t(view.text_key, lang, **view.text_args) in tip
        if reason == "invalid_host":
            assert ui_i18n.t("core_ind_tip_invalid_host", lang) in tip
    finally:
        ui_i18n.set_language("en")


def test_failed_observation_logs_paths_once():
    from ui_app import AtlasUI

    now = datetime.datetime.now().astimezone()
    status = dict(state="failed", reason="devices_missing", devices_file=CONFIG, updated_at=now.isoformat())

    class StatusPath:
        def read_text(self, **kwargs):
            import json
            return json.dumps(status)

    lines = []
    app = SimpleNamespace(
        _core_status_path=StatusPath(),
        _core_ind=ci.CoreIndicator(0, now, LOG),
        _clock=lambda: 1,
        _last_core_failure=None,
        _log_key=lambda *args, **kwargs: lines.append((args, kwargs)),
        _show_core=lambda view: None,
    )
    AtlasUI._observe_core(app, False, None)
    AtlasUI._observe_core(app, False, None)
    assert len(lines) == 1
    assert lines[0][1]["config"] == CONFIG and lines[0][1]["log"] == LOG


try:
    import tkinter as tk
    # Kept alive on purpose: destroying a Tk root before AtlasUI() segfaults Tk 9.
    _probe = tk.Tk()
    _probe.withdraw()
except Exception as exc:  # noqa: BLE001 — no display / no Tk
    tk = None
    _TK_SKIP = str(exc)
else:
    _TK_SKIP = None
    import ui_app  # noqa: E402
    from ui_widgets import fix_text_widths  # noqa: E402


@pytest.mark.skipif(_TK_SKIP is not None, reason=f"Tk display unavailable: {_TK_SKIP}")
def test_top_bar_tk_layout_and_bindings(monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    with patch.object(ui_app.AtlasUI, "_poll_status"), patch.object(ui_app.AtlasUI, "start_core_fallback"):
        app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    try:
        now = datetime.datetime.now().astimezone()
        view = ci.CoreIndicator(0, now, LOG).observe(
            False, None, 1, now,
            dict(state="failed", reason="devices_missing", devices_file=CONFIG, updated_at=now.isoformat()),
        )
        app._show_core(view)
        fix_text_widths(app, ui_i18n.LANGUAGES, ui_i18n.current_language, ui_i18n.set_language)
        app.update_idletasks()
        assert int(app.lbl_core_small.cget("wraplength")) == 0
        assert int(app.lbl_core_state.cget("wraplength")) >= 280
        assert app.lbl_core_small.winfo_height() < 40
        for widget in (app.light_core, app.lbl_core_caption, app.lbl_core_state, app.lbl_core_small):
            assert widget.bind("<Enter>") and widget.bind("<Leave>")
        small = app.lbl_core_small.cget("text")
        assert "\\" not in small and "Documents" not in small
    finally:
        app._is_closing = True
        try:
            app._file_log.close()
        except Exception:  # noqa: BLE001
            pass
        app.destroy()
        gc.collect()
