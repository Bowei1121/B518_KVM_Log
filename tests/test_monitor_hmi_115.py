"""Monitor UI layout, logo, icon and version rules from issue #115.

Pure checks (assets, generator output, version, build label) always run. Tk checks skip when there is no display.
The 100% / 125% / 150% / 200% checks fake the Tk scaling; the real Windows 11 look is FIELD_VERIFICATION H1.
"""

import ast
import gc
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST_APP = ROOT / "host-app"
sys.path.insert(0, str(HOST_APP))

import app_info  # noqa: E402
import ui_i18n  # noqa: E402

tk = pytest.importorskip("tkinter")


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- pure rules

def test_version_file_is_x_y_z_and_is_the_only_source():
    text = (HOST_APP / "VERSION").read_text(encoding="utf-8").strip()
    assert text == "0.1.00"
    assert app_info.version() == "0.1.00"


def test_title_text_uses_the_english_name_in_both_languages():
    assert app_info.title_text() == "ATE - LabVIEW Bridge Monitor v0.1.00"
    assert ui_i18n.STRINGS["en"]["win_title"] == ui_i18n.STRINGS["zh"]["win_title"] == \
        "ATE - LabVIEW Bridge Monitor v{version}"


def test_local_source_run_has_no_build_hash_so_it_shows_dev(monkeypatch):
    monkeypatch.setattr(app_info, "_build_info", lambda: (None, None, None))  # as if tools/write_build_info.py never ran
    assert app_info.is_ci_build() is False
    assert app_info.build_label() == "build dev"
    assert app_info.about_text() == "ATE - LabVIEW Bridge Monitor v0.1.00（build dev）"


def test_build_label_with_ci_hash_and_date(monkeypatch):
    monkeypatch.setattr(app_info, "_build_info", lambda: ("0.1.00", "736bb8b", "2026-10-22"))
    assert app_info.is_ci_build() is True
    assert app_info.build_label() == "build 736bb8b，2026-10-22"
    assert app_info.version() == "0.1.00"


def test_write_build_info_writes_version_hash_and_date(tmp_path, monkeypatch):
    writer = _load_tool("write_build_info")
    monkeypatch.setattr(writer, "OUT", tmp_path / "_build_info.py")
    monkeypatch.setattr(writer, "git_short_hash", lambda: "abc1234")
    writer.main()
    namespace = {}
    exec((tmp_path / "_build_info.py").read_text(encoding="utf-8"), namespace)
    assert namespace["VERSION"] == "0.1.00"
    assert namespace["BUILD_HASH"] == "abc1234"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", namespace["BUILD_DATE"])


def test_generated_build_info_is_not_committed():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "host-app/_build_info.py" in ignored
    assert not (HOST_APP / "_build_info.py").exists()


@pytest.mark.parametrize("name", ["foxlink_logo.png", "bridge_16.png", "bridge_32.png", "bridge_48.png",
                                  "bridge_256.png", "bridge.ico"])
def test_assets_are_committed(name):
    path = HOST_APP / "assets" / name
    assert path.is_file() and path.stat().st_size > 0, name


def test_committed_icon_pngs_match_the_ascii_drawing():
    from PIL import Image

    maker = _load_tool("make_bridge_icon")
    rows = maker.load_grid()
    for size in maker.SIZES:
        expected = maker.render(rows, size)
        actual = Image.open(HOST_APP / "assets" / f"bridge_{size}.png").convert("RGBA")
        assert actual.size == (size, size)
        assert actual.tobytes() == expected.tobytes(), f"bridge_{size}.png is stale"


def test_icon_ico_holds_every_size_and_the_bridge_colours():
    from PIL import Image

    with Image.open(HOST_APP / "assets" / "bridge.ico") as ico:
        assert sorted(ico.info["sizes"]) == [(16, 16), (32, 32), (48, 48), (256, 256)]
    maker = _load_tool("make_bridge_icon")
    assert maker.PALETTE["#"] == (0x00, 0x6D, 0xB6, 255)  # #006DB6 brand blue


def test_exe_packaging_uses_the_icon_and_bundles_assets_on_every_path():
    spec = (HOST_APP / "AtlasKVM.spec").read_text(encoding="utf-8")
    bat = (HOST_APP / "build_exe.bat").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    assert "bridge.ico" in spec and "('assets', 'assets')" in spec
    assert "--icon assets\\bridge.ico" in bat and "--add-data \"assets;assets\"" in bat
    assert "--icon assets/bridge.ico" in ci and "--add-data \"assets;assets\"" in ci
    assert "write_build_info.py" in bat and "write_build_info.py" in ci
    # the names stay AtlasKVM (issue #115 keeps exe / spec / artifact names)
    assert "name='AtlasKVM'" in spec and "--name AtlasKVM" in bat and "--name AtlasKVM" in ci


def test_ci_writes_build_info_before_packaging_and_tests_the_layout():
    ci = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    build_job = ci.split("  build:", 1)[1]
    assert build_job.index("write_build_info.py") < build_job.index("PyInstaller")
    assert build_job.index("write_build_info.py") < build_job.index("--self-test")
    test_job = ci.split("  build:", 1)[0]
    assert "tests/test_monitor_hmi_115.py" in test_job


def test_no_vs16_or_tk_checkbutton_in_the_hmi_sources():
    source = (HOST_APP / "device_settings_window.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<!t)tk\.Checkbutton\(", source)  # ttk.Checkbutton is the allowed one
    assert "ttk.Checkbutton(" in source


def test_no_fixed_button_widths_so_labels_never_get_cut():
    # Issue #115: a fixed width cut "Live view" and the Test button; buttons size to their text in both languages.
    for name in ("ui_app.py", "device_settings_window.py"):
        tree = ast.parse((HOST_APP / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "Button":
                assert all(kw.arg != "width" for kw in node.keywords), f"{name}:{node.lineno}"


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


def _fake_scaling(monkeypatch, scale):
    """Stand in for the Windows DPI call: set the Tk scaling the way a 100/125/150/200% screen would."""
    def apply(root):
        root.tk.call("tk", "scaling", scale)
        return scale
    monkeypatch.setattr(ui_app, "apply_windows_tk_scaling", apply)


@pytest.fixture
def make_ui(monkeypatch):
    created = []
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)

    def build(scale=1.0):
        _fake_scaling(monkeypatch, scale)
        app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
        created.append(app)
        app.update_idletasks()  # layout and mapping without the full event loop (see the note in the fixture)
        return app

    yield build
    for app in created:
        try:
            app._is_closing = True  # stops the scheduled poll and queue jobs before the window goes
            app._file_log.close()
            app.destroy()
        except tk.TclError:
            pass
    # Tk objects freed on a worker thread abort the process (macOS): collect here, on the main thread.
    created.clear()
    gc.collect()


def _log_lines_visible(app):
    # Tcl's font metrics directly: a tkinter Font object here made the next Tk root hang on macOS.
    line = int(app.tk.call("font", "metrics", app.log_text.cget("font"), "-linespace"))
    return app.log_text.winfo_height() // line


def _truncated(window):
    """Labels and buttons whose allocated size is smaller than what they ask for (their text is cut)."""
    cut = []
    for widget in _walk(window):
        if widget.winfo_class() not in ("Label", "Button", "TCheckbutton") or not widget.winfo_ismapped():
            continue
        if widget.winfo_width() < widget.winfo_reqwidth() or widget.winfo_height() < widget.winfo_reqheight():
            cut.append((str(widget), widget.cget("text")))
    return cut


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_log_is_visible_with_six_lines_at_every_scaling(make_ui, monkeypatch, scale):
    app = make_ui(scale)
    assert _log_lines_visible(app) >= 6, scale
    assert app.log_text.winfo_ismapped()


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_nothing_is_cut_off_in_either_language(make_ui, scale):
    app = make_ui(scale)
    for lang in ("en", "zh"):
        ui_i18n.set_language(lang)
        app.update_idletasks()
        assert _truncated(app) == [], (scale, lang)
        assert app.winfo_width() >= app.winfo_reqwidth()
        assert app.winfo_height() >= app.winfo_reqheight()
    ui_i18n.set_language("en")


def test_log_keeps_six_lines_even_when_the_window_is_shrunk_to_its_minimum(make_ui):
    app = make_ui(1.0)
    min_w, min_h = app.minsize()
    app.geometry(f"{min_w}x{min_h}")
    app.update_idletasks()
    assert _log_lines_visible(app) >= 6


def test_window_opens_at_85_percent_of_screen_height_and_content_width(make_ui):
    app = make_ui(1.0)
    screen_h = app.winfo_screenheight()
    assert app.winfo_height() >= min(int(screen_h * 0.85), screen_h)  # never shorter than 85% of the screen
    assert app.minsize()[0] == min(app.winfo_reqwidth(), app.winfo_screenwidth())


def test_top_row_is_logo_left_then_lang_and_gear_right(make_ui):
    app = make_ui(1.0)
    header = app.btn_lang.master
    assert app.lbl_logo.master is header and app.btn_device_settings.master is header
    assert app.lbl_logo.winfo_x() < app.btn_lang.winfo_x() < app.btn_device_settings.winfo_x()
    # same row: the gear's font is larger, so compare centre lines, not top edges
    lang_mid = app.btn_lang.winfo_y() + app.btn_lang.winfo_height() / 2
    gear_mid = app.btn_device_settings.winfo_y() + app.btn_device_settings.winfo_height() / 2
    assert abs(lang_mid - gear_mid) <= 6


def test_connection_row_keeps_refresh_and_lamp_and_has_no_gear(make_ui):
    app = make_ui(1.0)
    row = app.btn_connect.master
    assert app.light_core.master is row
    assert app.btn_device_settings.master is not row
    assert app.btn_connect.winfo_x() < app.light_core.winfo_x()


def test_logo_tooltip_names_the_app_version_and_build(make_ui):
    app = make_ui(1.0)
    assert app._logo_tip.current_text() == app_info.about_text()
    assert "v0.1.00" in app._logo_tip.current_text()
    assert "build dev" in app._logo_tip.current_text()


def test_logo_is_scaled_to_the_dpi_and_falls_back_to_text(make_ui, monkeypatch):
    monkeypatch.setattr(ui_app.AtlasUI, "winfo_fpixels", lambda self, _n: 144.0)  # 150% screen
    app = make_ui(1.0)
    assert app._logo_image.width() == round(137 * 1.5)
    assert app._logo_image.height() == round(35 * 1.5)

    monkeypatch.setattr(ui_app, "asset_path", lambda _name: ROOT / "no-such-logo.png")
    fallback = make_ui(1.0)
    assert fallback._logo_image is None
    assert str(fallback.lbl_logo.cget("text")) == "FOXLINK"


def test_log_opens_with_version_and_build_line(make_ui):
    app = make_ui(1.0)
    first = app.log_text.get("1.0", "1.end")
    assert "ATE - LabVIEW Bridge Monitor v0.1.00" in first
    assert app_info.build_label() in first


def test_log_wraps_long_lines_instead_of_scrolling_sideways(make_ui):
    app = make_ui(1.0)
    assert str(app.log_text.cget("wrap")) == "word"
    assert not any(w.winfo_class() == "Scrollbar" and str(w.cget("orient")) == "horizontal" for w in _walk(app))


def test_button_text_is_not_cut_in_the_live_view_row(make_ui):
    app = make_ui(1.0)
    for button in (app.btn_live_stream, app.btn_streamer_window, app.btn_snapshot):
        assert button.winfo_reqwidth() <= button.winfo_width() or not button.winfo_ismapped()


def test_empty_dashboard_says_what_to_do_in_both_languages(make_ui):
    app = make_ui(1.0)
    # Not connected yet: the hint explains why the list is blank.
    assert app._stations == []
    assert app.lbl_empty_hint.cget("text").startswith("Not connected")
    # Connected with no machines: the hint tells the operator to add one with the gear.
    app._update_status_ui({"status": "ok", "stations": []})
    assert app.lbl_empty_hint.cget("text") == "No machines yet. Press ⚙ at the top right to add one."
    app.on_toggle_language()
    assert app.lbl_empty_hint.cget("text") == "還沒有設定機台，請按右上角 ⚙ 新增。"
    app.on_toggle_language()


def test_empty_hint_goes_away_once_machines_are_shown(make_ui):
    app = make_ui(1.0)
    app._update_status_ui({"status": "ok", "stations": [{"device": "DFU-01", "station": "DFU", "status": "IDLE"}]})
    assert app.lbl_empty_hint is None
    app._update_status_ui({"status": "ok", "stations": []})
    assert app.lbl_empty_hint is not None


def test_window_icon_images_are_kept_on_the_root(make_ui):
    app = make_ui(1.0)
    images = getattr(app, "_atlas_icon_images")
    assert [img.width() for img in images] == [16, 32, 48]


def test_runtime_self_test_passes_from_source(make_ui):
    make_ui(1.0)  # a Tk root must exist for the image checks inside the self-test
    assert ui_app.runtime_self_test() == []


def test_device_settings_is_wide_enough_or_scrolls_sideways(make_ui, tmp_path):
    import device_settings_window

    app = make_ui(1.0)
    settings = device_settings_window.DeviceSettingsWindow(app, client=None, path=tmp_path / "devices.json")
    try:
        settings.update_idletasks()
        canvas = next(w for w in _walk(settings) if w.winfo_class() == "Canvas")
        assert canvas.cget("xscrollcommand"), "table has no horizontal scrollbar"
        # every row shows its checkbox as a ttk widget (tk.Checkbutton squashed on high DPI)
        settings._add_row()
        settings.update_idletasks()
        assert any(w.winfo_class() == "TCheckbutton" for w in _walk(settings))
        assert _truncated(settings.table) == []
    finally:
        settings.destroy()


def test_device_settings_has_a_test_button_that_is_not_cut_in_either_language(make_ui, tmp_path):
    import device_settings_window

    app = make_ui(1.0)
    settings = device_settings_window.DeviceSettingsWindow(app, client=None, path=tmp_path / "devices.json")
    try:
        settings._add_row()
        for lang in ("en", "zh"):
            ui_i18n.set_language(lang)
            settings.update_idletasks()
            assert _truncated(settings) == [], lang
    finally:
        ui_i18n.set_language("en")
        settings.destroy()
