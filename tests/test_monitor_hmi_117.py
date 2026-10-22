"""Monitor UI window placement, fixed label widths and mouse wheel from issue #117.

Pure checks always run. The Tk checks skip when there is no display. The work area (taskbar) and the window frame are
simulated, as the Windows call cannot run here; the real Windows 11 look is FIELD_VERIFICATION H1.
"""

import gc
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST_APP = ROOT / "host-app"
sys.path.insert(0, str(HOST_APP))

import ui_i18n  # noqa: E402
import window_placement as placement  # noqa: E402
import wheel_scroll  # noqa: E402

tk = pytest.importorskip("tkinter")

TASKBAR_LOGICAL = 48  # taskbar height at 100% (scaling 4/3); it grows with the Windows scaling
BASE_SCALE = 4 / 3


# ---------------------------------------------------------------- pure rules

class _FakeUser32:
    def __init__(self, area=None, metrics=None):
        self.area = area
        self.metrics = metrics or {}

    def SystemParametersInfoW(self, action, _param, rect_ref, _flags):
        if self.area is None:
            return 0
        rect = rect_ref._obj
        rect.left, rect.top, rect.right, rect.bottom = self.area
        return 1

    def GetSystemMetrics(self, index):
        return self.metrics[index]


class _FakeWinDll:
    def __init__(self, user32):
        self.user32 = user32


def test_work_area_comes_from_spi_getworkarea_on_windows():
    dll = _FakeWinDll(_FakeUser32(area=(0, 0, 1920, 1032)))
    assert placement.work_area(1920, 1080, windll=dll) == placement.Rect(0, 0, 1920, 1032)


def test_work_area_falls_back_to_the_whole_screen_when_the_call_fails():
    dll = _FakeWinDll(_FakeUser32(area=None))
    assert placement.work_area(1920, 1080, windll=dll) == placement.Rect(0, 0, 1920, 1080)


def test_work_area_is_the_whole_screen_off_windows(monkeypatch):
    monkeypatch.setattr(placement, "_windows_dll", lambda: None)  # as on macOS and Linux, whatever the host is
    assert placement.work_area(1440, 900) == placement.Rect(0, 0, 1440, 900)


def test_frame_is_zero_off_windows_and_from_metrics_on_windows(monkeypatch):
    monkeypatch.setattr(placement, "_windows_dll", lambda: None)
    assert placement.frame_size() == placement.NO_FRAME
    metrics = {placement.SM_CXFRAME: 8, placement.SM_CYFRAME: 8, placement.SM_CYCAPTION: 23,
               placement.SM_CXPADDEDBORDER: 4}
    frame = placement.frame_size(windll=_FakeWinDll(_FakeUser32(metrics=metrics)))
    assert frame == placement.Frame(left=12, right=12, top=35, bottom=12)


def test_open_geometry_sits_flush_top_right_and_keeps_the_frame_inside():
    area = placement.Rect(0, 0, 1920, 1032)
    frame = placement.Frame(8, 8, 31, 8)
    geometry = placement.open_geometry(area, frame, (1920, 1080), (843, 699))
    width, height, x, y = _parse(geometry)
    assert (x + width + frame.left + frame.right, y) == (1920, 0)  # right edge and top edge flush
    assert height + frame.top + frame.bottom <= area.height  # the title bar and border are inside the work area


def test_open_geometry_never_leaves_the_work_area_even_when_content_is_taller():
    area = placement.Rect(0, 0, 1920, 1032)
    frame = placement.Frame(8, 8, 31, 8)
    geometry = placement.open_geometry(area, frame, (1920, 1080), (843, 2000))
    width, height, _x, _y = _parse(geometry)
    assert height == area.height - frame.top - frame.bottom


def test_open_geometry_uses_half_width_and_85_percent_height_when_content_is_small():
    area = placement.Rect(0, 0, 2880, 1728)
    frame = placement.Frame(8, 8, 31, 8)
    width, height, _x, _y = _parse(placement.open_geometry(area, frame, (2880, 1800), (900, 500)))
    assert width == 1440
    assert height == min(int(1800 * 0.85), area.height - frame.top - frame.bottom)


def test_wheel_units_follow_windows_notches_and_small_mac_deltas():
    assert wheel_scroll.units_for(-120) == 1  # wheel down: scroll down one step
    assert wheel_scroll.units_for(120) == -1
    assert wheel_scroll.units_for(-240) == 2
    assert wheel_scroll.units_for(-1) == 1  # macOS sends small deltas: still one step
    assert wheel_scroll.units_for(3) == -1
    assert wheel_scroll.units_for(0) == 0


def _parse(geometry):
    size, _, rest = geometry.partition("+")
    width, height = (int(v) for v in size.split("x"))
    x, _, y = rest.partition("+")
    return width, height, int(x), int(y)


def _area(screen, scale):
    width, height = screen
    taskbar = round(TASKBAR_LOGICAL * scale / BASE_SCALE)
    return placement.Rect(0, 0, width, height - taskbar)


def _frame(scale):
    return placement.Frame(*(round(v * scale / BASE_SCALE) for v in (8, 8, 31, 8)))


# ---------------------------------------------------------------- Tk checks

try:
    # Kept alive on purpose: destroying a Tk root before AtlasUI() segfaults Tk 9 (repo .venv).
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402
import device_settings_window  # noqa: E402
import ui_widgets  # noqa: E402
from ui_widgets import iter_widgets  # noqa: E402

SUPPORTED = [
    ((1920, 1080), 4 / 3),
    ((2880, 1800), 4 / 3),
    ((2880, 1800), 5 / 3),
    ((2880, 1800), 2.0),
    ((2880, 1800), 8 / 3),
]
SUPPORTED_IDS = [f"{w}x{h}@{round(scale * 75)}%" for (w, h), scale in SUPPORTED]


def _simulate(monkeypatch, screen, scale):
    """This screen, this Tk scaling, a taskbar at the bottom and a Windows frame, as the real call would report them."""
    width, height = screen
    monkeypatch.setattr(ui_app.AtlasUI, "winfo_screenwidth", lambda self: width)
    monkeypatch.setattr(ui_app.AtlasUI, "winfo_screenheight", lambda self: height)
    area = _area(screen, scale)
    frame = _frame(scale)
    monkeypatch.setattr(placement, "work_area", lambda w, h, windll=None: area)
    monkeypatch.setattr(ui_app, "frame_size", lambda: frame)
    # the settings window sizes itself from its own screen (review R2-2): simulate that too
    monkeypatch.setattr(device_settings_window.DeviceSettingsWindow, "winfo_screenwidth", lambda self: width)
    monkeypatch.setattr(device_settings_window.DeviceSettingsWindow, "winfo_screenheight", lambda self: height)

    # wm maxsize is per toplevel and defaults to the runner's real 1024x768 screen: lift it for the settings window too
    original_build = device_settings_window.DeviceSettingsWindow._build

    def build_unlimited(self):
        self.wm_maxsize(10000, 10000)
        original_build(self)

    monkeypatch.setattr(device_settings_window.DeviceSettingsWindow, "_build", build_unlimited)

    def apply(root):
        root.wm_maxsize(10000, 10000)  # the runner's real 1024×768 must not cap the window
        root.tk.call("tk", "scaling", scale)
        return scale

    monkeypatch.setattr(ui_app, "apply_windows_tk_scaling", apply)
    return area, frame


@pytest.fixture
def make_ui(monkeypatch):
    created = []
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)

    def build(screen=(2880, 1800), scale=4 / 3):
        _simulate(monkeypatch, screen, scale)
        app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
        created.append(app)
        app.update_idletasks()
        return app

    yield build
    for app in created:
        try:
            app._is_closing = True
            app._file_log.close()
            app.destroy()
        except tk.TclError:
            pass
    created.clear()
    gc.collect()


def _spy(monkeypatch, cls):
    requested = []
    original = cls.geometry

    def spy(self, *args):
        if args:
            requested.append(args[0])
        return original(self, *args)

    monkeypatch.setattr(cls, "geometry", spy)
    return requested


def _log_lines_visible(app):
    line = int(app.tk.call("font", "metrics", app.log_text.cget("font"), "-linespace"))
    return app.log_text.winfo_height() // line


@pytest.mark.parametrize(("screen", "scale"), SUPPORTED, ids=SUPPORTED_IDS)
def test_window_opens_flush_top_right_and_inside_the_work_area(make_ui, monkeypatch, screen, scale):
    requested = _spy(monkeypatch, ui_app.AtlasUI)
    make_ui(screen, scale)
    area, frame = _area(screen, scale), _frame(scale)
    width, height, x, y = _parse(requested[-1])
    assert x + width + frame.left + frame.right == area.x + area.width, requested[-1]  # right edge flush
    assert y == area.y, requested[-1]  # top edge flush
    assert y + height + frame.top + frame.bottom <= area.y + area.height, requested[-1]  # nothing under the taskbar


@pytest.mark.parametrize(("screen", "scale"), SUPPORTED, ids=SUPPORTED_IDS)
def test_six_log_lines_stay_visible_after_the_placement(make_ui, screen, scale):
    app = make_ui(screen, scale)
    assert _log_lines_visible(app) >= 6


def test_small_usable_height_shrinks_the_preview_and_keeps_the_log(make_ui, monkeypatch):
    requested = _spy(monkeypatch, ui_app.AtlasUI)
    app = make_ui((1366, 768), 4 / 3)  # 720 px usable: the full layout (about 700 px) does not leave room for the frame
    _width, height, _x, _y = _parse(requested[-1])
    frame = _frame(4 / 3)
    assert height <= _area((1366, 768), 4 / 3).height - frame.top - frame.bottom
    assert int(app.canvas_preview.cget("height")) < 270  # the live view gave up height first
    assert _log_lines_visible(app) >= 6


def test_enlarging_the_window_enlarges_the_live_view(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    app.update()  # geometry() is applied by the window manager: update_idletasks() alone leaves the old size on Windows
    before = app.canvas_preview.winfo_width()
    app.geometry(f"{app.winfo_width() + 300}x{app.winfo_height() + 150}")
    app.update()
    assert app.canvas_preview.winfo_width() > before, f"{before} -> {app.canvas_preview.winfo_width()}, maxsize {app.wm_maxsize()}"


def test_full_preview_is_kept_when_the_usable_height_is_enough(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    assert int(app.canvas_preview.cget("height")) == 270


def test_language_switch_never_changes_the_window_geometry(make_ui, monkeypatch):
    monkeypatch.setattr(ui_i18n, "save_language", lambda lang: True)
    app = make_ui((1920, 1080), 4 / 3)
    requested = _spy(monkeypatch, ui_app.AtlasUI)
    before = app.wm_geometry()
    calls = len(requested)
    app.on_toggle_language()
    app.update_idletasks()
    assert len(requested) == calls
    assert app.wm_geometry() == before
    app.on_toggle_language()
    ui_i18n.set_language("en")


def _layout(window):
    """Position and size of every widget under ``window``, plus the window itself."""
    window.update_idletasks()
    return {str(w): (w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()) for w in iter_widgets(window)}


def test_language_switch_keeps_every_label_and_button_where_it_was(make_ui, monkeypatch, tmp_path):
    monkeypatch.setattr(ui_i18n, "save_language", lambda lang: True)
    app = make_ui((1920, 1080), 4 / 3)
    stations = [{"device": f"{name}-{n:02d}", "station": name, "status": "IDLE"}
                for name in ("DFU", "FCT", "BT") for n in (1, 2)]
    app._update_status_ui({"status": "ok", "stations": stations})
    settings = device_settings_window.DeviceSettingsWindow(app, client=None, path=tmp_path / "devices.json")
    settings._add_row()
    from ui_app import MatchResultsWindow
    matches = MatchResultsWindow(app, app._templates, "DFU-01")
    windows = {"main": app, "device settings": settings, "match results": matches}
    try:
        ui_i18n.set_language("en")
        before = {name: _layout(w) for name, w in windows.items()}
        caption_en = app.lbl_core_caption.cget("text")
        app.on_toggle_language()
        after = {name: _layout(w) for name, w in windows.items()}
        assert app.lbl_core_caption.cget("text") != caption_en  # the words really changed
        for name in windows:
            moved = {k: (before[name][k], after[name].get(k)) for k in before[name] if before[name][k] != after[name].get(k)}
            assert moved == {}, f"{name} changed after the language switch: {moved}"
    finally:
        ui_i18n.set_language("en")
        settings.destroy()
        matches.destroy()


def test_device_settings_path_wraps_inside_the_window_and_is_never_split_mid_name(make_ui, tmp_path):
    app = make_ui((1920, 1080), 4 / 3)
    deep = tmp_path / ("a_very_long_folder_name_" * 4) / "devices.json"
    settings = device_settings_window.DeviceSettingsWindow(app, client=None, path=deep)
    try:
        settings.geometry("420x520")
        settings._size_head_labels(420)
        settings.update_idletasks()
        shown = str(settings.lbl_path.cget("text"))
        assert "\n" not in shown
        assert shown.endswith("devices.json") and "…" in shown  # the file name stays whole, the front is cut
        assert int(settings.tk.call("font", "measure", device_settings_window.DS_FONT, shown)) <= 420 - 40
    finally:
        settings.destroy()


# ---------------------------------------------------------------- mouse wheel

def _center(widget):
    return widget.winfo_rootx() + max(2, widget.winfo_width() // 2), widget.winfo_rooty() + max(2, widget.winfo_height() // 2)


def _wheel(widget, delta, state=0):
    x, y = _center(widget)
    widget.event_generate("<MouseWheel>", delta=delta, state=state, rootx=x, rooty=y)
    widget.update_idletasks()


def _fill_log(app, lines=200):
    app.log_text.insert("end", "\n".join(f"line {n}" for n in range(lines)))
    app.update_idletasks()


def test_log_scrolls_with_the_wheel_under_the_pointer_even_when_another_widget_has_focus(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    _fill_log(app)
    app.e_core_addr.focus_force()  # Windows sends the wheel to the focused widget: the Log must still move
    app.update_idletasks()
    top_before = app.log_text.yview()[0]
    _wheel(app.log_text, -120)  # Windows notch down
    assert app.log_text.yview()[0] > top_before


def test_wheel_up_moves_the_log_back(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    _fill_log(app)
    app.log_text.yview_moveto(0.5)
    app.update_idletasks()
    top = app.log_text.yview()[0]
    _wheel(app.log_text, 120)
    assert app.log_text.yview()[0] < top


def test_machine_list_scrolls_with_the_wheel(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    stations = [{"device": f"DFU-{n:02d}", "station": "DFU", "status": "IDLE"} for n in range(1, 31)]
    app._update_status_ui({"status": "ok", "stations": stations})
    app.update_idletasks()
    top = app._dash_canvas.yview()[0]
    _wheel(app._dash_canvas, -120)
    assert app._dash_canvas.yview()[0] > top


def test_settings_table_scrolls_down_and_sideways_with_shift(make_ui, tmp_path):
    app = make_ui((1920, 1080), 4 / 3)
    settings = device_settings_window.DeviceSettingsWindow(app, client=None, path=tmp_path / "devices.json")
    try:
        settings.geometry("200x400")  # narrower than any table, so there is always something to scroll sideways
        for _ in range(30):
            settings._add_row()
        settings.update()  # the window manager applies geometry() here; update_idletasks() would not on Windows
        canvas = next(w for w in iter_widgets(settings) if w.winfo_class() == "Canvas")
        info = (f"canvas {canvas.winfo_width()}px, table {settings.table.winfo_reqwidth()}px, window "
                f"{settings.winfo_width()}px, maxsize {settings.wm_maxsize()}")
        assert settings.table.winfo_reqwidth() > canvas.winfo_width(), "premise: the table is wider than the canvas; " + info
        assert canvas.xview()[1] < 1.0, "the table must be wider than its window, or there is nothing to scroll sideways; " + info
        top = canvas.yview()[0]
        _wheel(canvas, -120)
        assert canvas.yview()[0] > top
        left = canvas.xview()[0]
        _wheel(canvas, -120, state=wheel_scroll.SHIFT_MASK)  # Shift + wheel: sideways
        assert canvas.xview()[0] > left, info
    finally:
        settings.destroy()


def test_match_list_scrolls_with_the_wheel(make_ui, monkeypatch):
    from ui_app import MatchResultsWindow

    app = make_ui((1920, 1080), 4 / 3)
    matches = MatchResultsWindow(app, app._templates, "DFU-01")
    try:
        for n in range(80):
            matches.listbox.insert("end", f"item {n}")
        matches.update_idletasks()
        top = matches.listbox.yview()[0]
        _wheel(matches.listbox, -120)
        assert matches.listbox.yview()[0] > top
    finally:
        matches.destroy()


def test_wheel_over_a_plain_button_scrolls_nothing(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    _fill_log(app)
    top = app.log_text.yview()[0]
    _wheel(app.btn_connect, -120)
    assert app.log_text.yview()[0] == top


def test_linux_button_4_and_5_are_bound_and_scroll_the_log(make_ui):
    # Checked through the handler itself: Button-4/5 are X11 events, and not every platform's Tk synthesises them.
    app = make_ui((1920, 1080), 4 / 3)
    _fill_log(app)
    assert app.bind_all("<Button-4>") and app.bind_all("<Button-5>")
    x, y = _center(app.log_text)
    event = types.SimpleNamespace(x_root=x, y_root=y, state=0)
    app.log_text.yview_moveto(0.5)
    app.update_idletasks()
    top = app.log_text.yview()[0]
    wheel_scroll._scroll(app, event, -120)  # Button-5 is "down"
    assert app.log_text.yview()[0] > top
    wheel_scroll._scroll(app, event, 120)  # Button-4 is "up"
    assert app.log_text.yview()[0] == pytest.approx(top, abs=1e-6)


def test_pattern_cropper_keeps_its_size_and_places_across_a_language_switch(make_ui):
    # The cropper is all ttk widgets; before the fix its window grew and shrank with the language (#117 review).
    from pattern_tools import PatternCropper

    app = make_ui((1920, 1080), 4 / 3)
    cropper = PatternCropper(app, app._templates)
    try:
        ui_i18n.set_language("en")
        before = _layout(cropper)
        ui_i18n.set_language("zh")
        after = _layout(cropper)
        ui_i18n.set_language("en")
        moved = {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)}
        assert moved == {}, f"cropper changed after the language switch: {moved}"
    finally:
        ui_i18n.set_language("en")
        cropper.destroy()


LONG_RESULT = "Result: " + "channel 1 and channel 2 are still testing; " * 6
LONG_RESULT_ZH = "診斷結果：" + "通道 1 與通道 2 仍在測試中；" * 8
LONG_SNAPSHOT = "Snapshot 1920x1080 at 12:34:56 from DFU-01, " * 3


def _show_long_results(app):
    """The longest run-time texts, set the way the app sets them (cut to the fixed lines, whole text in the hint)."""
    app._set_info(app.lbl_check_result, lambda: LONG_RESULT if ui_i18n.current_language() == "en" else LONG_RESULT_ZH,
                  ui_app.MUTED_FG)
    app._set_info(app.lbl_preview_info, lambda: LONG_SNAPSHOT, ui_app.MUTED_FG)
    app.update_idletasks()


def test_long_run_time_result_is_cut_to_three_lines_and_the_whole_text_is_in_the_hint(make_ui):
    # Review R3-1: the result box has a fixed height; what does not fit is cut with "…" (the whole text is in the Log).
    app = make_ui((1920, 1080), 4 / 3)
    label = app.lbl_check_result
    height_before = label.winfo_reqheight()
    _show_long_results(app)
    line = int(app.tk.call("font", "metrics", label.cget("font"), "-linespace"))
    assert label.winfo_reqheight() == height_before, "the box changed size with its text"
    assert label.winfo_reqheight() <= 3 * line + 6
    assert str(label.cget("text")).endswith("…") and label.cget("text") != LONG_RESULT
    assert ui_widgets.full_text(label) == LONG_RESULT  # the hover hint shows everything
    assert str(app.lbl_preview_info.cget("text")).endswith("…")
    assert ui_widgets.full_text(app.lbl_preview_info) == LONG_SNAPSHOT
    assert "\n" not in str(app.lbl_preview_info.cget("text"))  # the snapshot line is one line


def test_a_short_result_is_shown_whole(make_ui):
    app = make_ui((1920, 1080), 4 / 3)
    app._set_info(app.lbl_check_result, lambda: "OK", ui_app.OK_FG)
    assert app.lbl_check_result.cget("text") == "OK" and ui_widgets.full_text(app.lbl_check_result) == ""


def _cut_texts(window):
    """Labels and Buttons whose measured text does not fit what they were given (review R2-1, R3-1).

    A one-line widget is cut when its text is wider than the widget; a wrapping one when its lines need more height
    than it has. Measured with font metrics, not the widget's own request, which always agrees with itself.
    """
    cut = []
    for widget in iter_widgets(window):
        if widget.winfo_class() not in ("Label", "TLabel", "Button", "TButton") or not widget.winfo_ismapped():
            continue
        text = str(widget.cget("text"))
        if not text:
            continue
        font = ui_widgets._font_of(widget)
        wrap = int(str(widget.cget("wraplength")) or 0) if widget.winfo_class() in ("Label", "TLabel") else 0
        if wrap:
            if widget.winfo_height() < widget.winfo_reqheight():
                cut.append((str(widget), text, widget.winfo_height(), widget.winfo_reqheight()))
        else:
            width = max(int(widget.tk.call("font", "measure", font, row)) for row in text.split("\n"))
            if width > widget.winfo_width():
                cut.append((str(widget), text, width, widget.winfo_width()))
    return cut


def test_long_dynamic_texts_with_nine_machines_are_never_cut(make_ui):
    # Review R2-1 part 3 / R3-2a: a long run-time result, a long snapshot line and nine machines at once.
    app = make_ui((1920, 1080), 4 / 3)
    stations = [{"device": f"{name}-{n:02d}", "station": name, "status": "IDLE"}
                for name in ("DFU", "FCT", "BT") for n in (1, 2, 3)]
    app._update_status_ui({"status": "ok", "stations": stations})
    _show_long_results(app)
    app.update()
    assert _cut_texts(app) == []
    app.on_toggle_language()
    app.update()
    assert _cut_texts(app) == []
    ui_i18n.set_language("en")


@pytest.mark.parametrize(("screen", "scale"), SUPPORTED, ids=SUPPORTED_IDS)
def test_long_results_keep_every_widget_in_place_across_a_language_switch(make_ui, monkeypatch, screen, scale):
    # Review R3-1: the same long result in the other language must not move or resize anything on the main window.
    monkeypatch.setattr(ui_i18n, "save_language", lambda lang: True)
    app = make_ui(screen, scale)
    stations = [{"device": f"{name}-{n:02d}", "station": name, "status": "IDLE"}
                for name in ("DFU", "FCT", "BT") for n in (1, 2, 3)]
    app._update_status_ui({"status": "ok", "stations": stations})
    ui_i18n.set_language("en")
    _show_long_results(app)
    app.update()
    before = _layout(app)
    assert _cut_texts(app) == []
    app.on_toggle_language()
    app.update()
    after = _layout(app)
    moved = {k: (before[k], after.get(k)) for k in before if before[k] != after.get(k)}
    ui_i18n.set_language("en")
    assert moved == {}, f"{len(moved)} widgets moved after the language switch: {moved}"
    assert _cut_texts(app) == []
