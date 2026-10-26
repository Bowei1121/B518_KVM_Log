"""Station list scrollbar for more than 12 devices (issue #126, spec v5.3 §14.10–§14.12)."""
import gc
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import ui_i18n  # noqa: E402
from station_list_hint import first_of_many, list_hint_text  # noqa: E402

RED, GREY = "#B71C1C", "#546E7A"
IDS = [f"DFU-{i:02d}" for i in range(1, 11)] + ["FCT-01", "FCT-02", "BT-01", "BT-02"]  # spec scroll-alarm scenario


# ---------------------------------------------------------------------------------------------------------------
# Hint text (Tk-free): longest form that fits, shortened in the §14.10 order, never cut
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_hint_shortens_in_spec_order_and_never_cuts(lang):
    ui_i18n.set_language(lang)
    t = ui_i18n.t
    full, short = t("list_more_below", n=2), t("list_more_down", n=2)
    every = t("list_abnormal", k=2, ids=t("sep_list").join(["BT-01", "BT-02"]))
    first = first_of_many("BT-01", 1)
    chain = [full + every, short + every, short + t("list_abnormal", k=2, ids=first),
             t("list_abnormal_only", arrow="▼", k=2, ids=first)]
    for expected in chain:
        assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-01", "BT-02"], lambda s: s == expected) == expected
    assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-01", "BT-02"], lambda s: False) == chain[-1]

    one = t("list_abnormal", k=1, ids="BT-02")
    for expected in [full + one, short + one, t("list_abnormal_only", arrow="▼", k=1, ids="BT-02")]:
        assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-02"], lambda s: s == expected) == expected
    assert list_hint_text([], ["A", "B"], [], [], lambda s: s == short) == short
    assert list_hint_text([], ["A", "B"], [], [], lambda s: False) == short
    assert list_hint_text([], [], [], [], lambda s: True) == ""


def test_hint_literal_strings_from_the_spec():
    def fits(_s):
        return True
    assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-02"], fits) == "▼ 2 more below · 1 abnormal (BT-02)"
    assert list_hint_text(["A", "B"], [], [], [], fits) == "▲ 2 more above"
    assert list_hint_text(["A"], ["B", "C"], [], [], fits) == "▲ 1 ▼ 2 more"
    assert list_hint_text(["A"], ["BT-02"], [], ["BT-02"], lambda s: s.startswith("▼ 1 ab")) == "▼ 1 abnormal (BT-02)"
    assert list_hint_text(["DFU-05"], [], ["DFU-05"], [], lambda s: False) == "▲ 1 abnormal (DFU-05)"
    assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-01", "BT-02"], lambda s: False) == "▼ 2 abnormal (BT-01 +1)"
    ui_i18n.set_language("zh")
    assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-02"], fits) == "▼ 下方還有 2 台 · 1 台異常（BT-02）"
    assert list_hint_text([], ["BT-01", "BT-02"], [], ["BT-02"], lambda s: "下方" not in s) == "▼ 還有 2 台 · 1 台異常（BT-02）"
    assert list_hint_text(["A", "B"], [], [], [], fits) == "▲ 上方還有 2 台"
    assert list_hint_text(["A"], ["B"], [], [], fits) == "▲ 1 台 ▼ 1 台"
    assert list_hint_text([], ["A", "B"], [], ["A", "B"], lambda s: s.startswith("▼ 還有 2 台 · 2 台異常（A 等")) \
        == "▼ 還有 2 台 · 2 台異常（A 等）"


# ---------------------------------------------------------------------------------------------------------------
# Tk panel
# ---------------------------------------------------------------------------------------------------------------
tk = pytest.importorskip("tkinter")
try:
    _probe = tk.Tk()
    _probe.withdraw()
except tk.TclError:
    pytest.skip("no Tk display", allow_module_level=True)

import ui_app  # noqa: E402


@pytest.fixture
def ui(monkeypatch, request):
    scale = getattr(request, "param", 4 / 3)
    old = _probe.tk.call("tk", "scaling")

    def apply(root):
        root.wm_maxsize(10000, 10000)
        root.tk.call("tk", "scaling", scale)
        return scale

    monkeypatch.setattr(ui_app, "apply_windows_tk_scaling", apply)
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    app.client.start_status_poll = lambda callback: None
    app.wm_maxsize(10000, 10000)
    try:
        yield app
    finally:
        app._is_closing = True
        app._file_log.close()
        app.destroy()
        _probe.tk.call("tk", "scaling", old)
        del app
        gc.collect()


def rows(n=14, frozen=("BT-02",)):
    ids = IDS[:n] if n <= len(IDS) else IDS + [f"BT-{i:02d}" for i in range(3, n - len(IDS) + 3)]
    return [{"device": d, "station": d.split("-")[0], "status": "FROZEN" if d in frozen else "IDLE"} for d in ids]


def show(ui, stations):
    ui._update_status_ui({"status": "ok", "stations": stations})
    ui.geometry("960x1010")
    ui.update()


def view_top(ui):
    return ui.station_canvas.canvasy(0)


def wheel(ui, widget, delta, x=40, y=20):
    ui.event_generate("<MouseWheel>", delta=delta,
                      rootx=widget.winfo_rootx() + x, rooty=widget.winfo_rooty() + y)
    ui.update()


def fully_visible(ui):
    top = view_top(ui)
    return [d for d, y in ui._list_row_tops.items() if y >= top and y + 56 <= top + 728]


@pytest.mark.parametrize("count", [9, 12])
def test_twelve_or_fewer_keep_an_empty_track_and_cannot_scroll(ui, count):
    show(ui, rows(count))
    cells = ui.stations_container.winfo_children()
    assert len(cells) == 12
    assert all((c.winfo_width(), c.winfo_height()) == (176, 56) for c in cells)
    assert ui.station_canvas.winfo_height() == 728
    assert (ui.station_track.winfo_width(), ui.station_track.winfo_height()) == (16, 728)
    assert ui.station_track.winfo_ismapped()
    assert not ui.station_track.find_withtag("thumb")
    assert not ui.station_track.find_withtag("tick")
    assert ui.lbl_list_hint.cget("text") == ""
    wheel(ui, ui._station_widgets["DFU-01"]["card"], -120)
    assert view_top(ui) == 0


@pytest.mark.parametrize("count", [13, 14, 20])
def test_more_than_twelve_draws_all_rows_and_scrolls_one_row_per_notch(ui, count):
    show(ui, rows(count, frozen=()))
    assert list(ui._station_widgets) == [r["device"] for r in rows(count)]
    assert len(ui.stations_container.winfo_children()) == count
    assert len(fully_visible(ui)) == 12
    assert ui._list_above == [] and len(ui._list_below) == count - 12
    x0, y0, x1, y1 = ui.station_track.coords("thumb")
    assert x1 - x0 == 13 and y1 - y0 >= 40 and y0 == 0
    card = ui._station_widgets["DFU-01"]["card"]
    wheel(ui, card, -120)
    assert view_top(ui) == 60
    assert len(fully_visible(ui)) == 12
    wheel(ui, ui._station_widgets["DFU-03"]["card"], 120)
    assert view_top(ui) == 0


def test_off_screen_abnormal_device_gets_a_red_tick_and_red_hint(ui):
    show(ui, rows(14))
    assert ui._list_below == ["BT-01", "BT-02"]
    # The full "▼ 2 more below · 1 abnormal (BT-02)" is wider than 188 px in 12 px bold (spec: 224 px), so it
    # drops "below" first, as in spec shot v5.3-01.
    assert ui._list_hint_font.measure("▼ 2 more below · 1 abnormal (BT-02)") > 188
    assert ui.lbl_list_hint.cget("text") == "▼ 2 more · 1 abnormal (BT-02)"
    # Drawn on one line and wide enough for the whole text (the #117 width lock must not shrink it to 1 char).
    assert int(str(ui.lbl_list_hint.cget("wraplength")) or 0) == 0
    hint_top = ui.lbl_list_hint.winfo_rooty()
    assert hint_top == ui.station_canvas.winfo_rooty() + 728  # right under the list, not at the panel bottom
    assert ui.lbl_list_hint.winfo_reqwidth() >= ui._list_hint_font.measure(ui.lbl_list_hint.cget("text"))
    assert ui.lbl_list_hint.cget("fg") == RED
    assert ui._list_hint_font.measure(ui.lbl_list_hint.cget("text")) <= 188
    assert ui.lbl_list_hint.winfo_ismapped() and ui.lbl_list_hint.master.winfo_height() == 16
    ticks = ui.station_track.find_withtag("tick")
    assert len(ticks) == 1
    x0, y0, x1, y1 = ui.station_track.coords(ticks[0])
    assert (x0, x1, y1 - y0) == (1, 10, 6)
    centre = (ui._list_row_tops["BT-02"] + 28) / ui._list_content_h * 728
    assert abs((y0 + y1) / 2 - centre) <= 1
    assert ui.station_track.itemcget(ticks[0], "fill") == RED

    ui.station_canvas.yview_moveto(1.0)  # the operator scrolls to the bottom
    ui.update()
    assert "BT-02" in fully_visible(ui)
    assert not ui.station_track.find_withtag("tick")
    assert ui.lbl_list_hint.cget("text") == "▲ 2 more above"
    assert ui.lbl_list_hint.cget("fg") == GREY


def test_normal_off_screen_rows_give_a_grey_hint_in_both_languages(ui):
    show(ui, rows(14, frozen=()))
    assert ui.lbl_list_hint.cget("text") == "▼ 2 more below"
    assert ui.lbl_list_hint.cget("fg") == GREY
    assert not ui.station_track.find_withtag("tick")
    ui.on_toggle_language()
    assert ui.lbl_list_hint.cget("text") == "▼ 下方還有 2 台"
    show(ui, rows(14))  # BT-02 freezes off-screen: the hint turns red at once
    # Which form fits depends on the CJK font width; it is one of the §14.12 forms and is never cut.
    assert ui.lbl_list_hint.cget("text") in ("▼ 下方還有 2 台 · 1 台異常（BT-02）", "▼ 還有 2 台 · 1 台異常（BT-02）",
                                             "▼ 1 台異常（BT-02）")
    assert ui.lbl_list_hint.cget("fg") == RED


def test_partly_visible_rows_count_as_off_screen(ui):
    show(ui, rows(14))  # BT-02 frozen
    # Stop half a row off the 60 px steps (a thumb drag, an extra station gap): 90 px down the list.
    ui.station_canvas.configure(yscrollincrement=1)
    ui.station_canvas.yview_moveto(90 / ui._list_content_h)
    ui.update()
    top = view_top(ui)
    assert top == 90
    tops = ui._list_row_tops
    assert tops["DFU-02"] < top < tops["DFU-02"] + 56  # DFU-02 half hidden at the top
    assert tops["BT-02"] < top + 728 < tops["BT-02"] + 56  # BT-02 half shown at the bottom
    assert ui._list_above == ["DFU-01", "DFU-02"]
    assert ui._list_below == ["BT-02"]
    assert len(ui.station_track.find_withtag("tick")) == 1
    assert "1 abnormal (BT-02)" in ui.lbl_list_hint.cget("text")
    assert ui.lbl_list_hint.cget("fg") == RED


def test_program_never_scrolls_the_list(ui):
    show(ui, rows(20, frozen=()))
    wheel(ui, ui._station_widgets["DFU-01"]["card"], -240)
    assert view_top(ui) == 120
    assert 0 < view_top(ui) < ui._list_content_h - ui.station_canvas.winfo_height()
    pos = ui.station_canvas.yview()
    below = rows(20)[-1]["device"]
    visible = fully_visible(ui)[0]
    assert below in ui._list_below and "DFU-01" in ui._list_above

    def unchanged():
        ui.update()
        assert ui.station_canvas.yview() == pos

    show(ui, rows(20, frozen=(below,)))
    unchanged()
    show(ui, rows(20, frozen=("DFU-01",)))
    unchanged()
    for device in (below, "DFU-01", visible):
        ui._select_device(device)
        unchanged()
    ui.combo_device.set(below)
    ui.combo_device.event_generate("<<ComboboxSelected>>")
    unchanged()
    ui._station_widgets[visible]["card"].event_generate("<Button-1>")
    unchanged()
    ui._update_status_ui({"status": "error"})  # Core disconnect
    unchanged()
    show(ui, rows(20, frozen=()))  # Core back
    unchanged()
    for _ in range(2):
        ui.on_toggle_language()
        unchanged()
    # A changed device list keeps the operator's position.
    show(ui, rows(21, frozen=()))
    assert view_top(ui) == 120


def test_row_titles_do_not_overlap_online_lamps(ui):
    show(ui, rows(14))
    for widgets in ui._station_widgets.values():
        title, lamp = widgets["title"], widgets["online_lamp"]
        assert title.winfo_x() + title.winfo_width() <= lamp.winfo_x()


def test_drag_stops_when_the_thumb_disappears(ui, monkeypatch):
    errors = []
    monkeypatch.setattr(ui, "report_callback_exception", lambda *args: errors.append(args))
    show(ui, rows(14))
    thumb = ui.station_track.coords("thumb")
    ui.station_track.event_generate("<Button-1>", x=8, y=int(thumb[1]) + 5)
    assert ui._track_drag is not None
    show(ui, rows(12))
    assert not ui.station_track.coords("thumb")
    ui.station_track.event_generate("<B1-Motion>", x=8, y=30)
    ui.update()
    assert errors == []
    assert ui._track_drag is None


def test_wheel_over_track_and_hint_scrolls_the_list(ui):
    show(ui, rows(14))
    wheel(ui, ui.station_track, -120, x=8)
    assert view_top(ui) == 60
    wheel(ui, ui.lbl_list_hint, -120, x=8, y=8)
    assert view_top(ui) == 120


def test_track_click_pages_and_thumb_drag_scrolls(ui):
    show(ui, rows(20, frozen=()))
    ui.station_track.event_generate("<Button-1>", x=8, y=727)
    ui.station_track.event_generate("<ButtonRelease-1>", x=8, y=727)
    ui.update()
    assert view_top(ui) > 0
    x0, y0, x1, y1 = ui.station_track.coords("thumb")
    ui.station_track.event_generate("<Button-1>", x=8, y=int(y0) + 5)
    assert ui.station_track.itemcget("thumb", "fill") == "#607D8B"
    ui.station_track.event_generate("<B1-Motion>", x=8, y=0)
    ui.station_track.event_generate("<ButtonRelease-1>", x=40, y=0)
    ui.update()
    assert view_top(ui) == 0
    assert ui.station_track.itemcget("thumb", "fill") == "#90A4AE"


@pytest.mark.parametrize("count", [12, 14])
def test_more_than_twelve_logs_one_info_line(ui, count, monkeypatch):
    calls = []
    original = ui._append_log

    def record(en, zh="", level="info"):
        calls.append((en, zh, level))
        return original(en, zh, level)

    monkeypatch.setattr(ui, "_append_log", record)
    show(ui, rows(count))
    show(ui, rows(count))  # the same list again: no new line
    logged = [c for c in calls if "devices configured" in c[0]]
    if count <= 12:
        assert logged == []
    else:
        assert logged == [("14 devices configured; list shows 12 rows, scroll for the rest",
                           "設定了 14 台設備；清單一次顯示 12 列，其餘請用捲軸查看", "info")]


# ---------------------------------------------------------------------------------------------------------------
# §14.10 (d) station-group gaps and (e) abnormal devices both above and below (designer, 2026-10-26)
# ---------------------------------------------------------------------------------------------------------------
# (e) changed by the user on 2026-10-26: both sides abnormal -> the devices only, no direction (they scroll to find
# the red rows). Fallbacks in order, never cut: every id, then the first id + the rest, then the count.
DIRECTION_WORDS = ("▲", "▼", "above", "below", "up", "down", "上方", "下方", "上面", "下面")
BOTH_SIDES = [
    # (above, below, abnormal above, abnormal below, {lang: (full, first + rest, count)}): 2, 3 and 6 abnormal
    (["DFU-05"], ["BT-02"], ["DFU-05"], ["BT-02"],
     {"en": ("DFU-05, BT-02 abnormal", "DFU-05 +1 abnormal", "2 abnormal"),
      "zh": ("DFU-05、BT-02 異常", "DFU-05 等 2 台異常", "2 台異常")}),
    (["DFU-04", "DFU-05"], ["BT-01", "BT-02"], ["DFU-04", "DFU-05"], ["BT-02"],
     {"en": ("DFU-04, DFU-05, BT-02 abnormal", "DFU-04 +2 abnormal", "3 abnormal"),
      "zh": ("DFU-04、DFU-05、BT-02 異常", "DFU-04 等 3 台異常", "3 台異常")}),
    (["DFU-01", "DFU-02", "DFU-03"], ["BT-01", "BT-02", "BT-03"], ["DFU-01", "DFU-02", "DFU-03"],
     ["BT-01", "BT-02", "BT-03"],
     {"en": ("DFU-01, DFU-02, DFU-03, BT-01, BT-02, BT-03 abnormal", "DFU-01 +5 abnormal", "6 abnormal"),
      "zh": ("DFU-01、DFU-02、DFU-03、BT-01、BT-02、BT-03 異常", "DFU-01 等 6 台異常", "6 台異常")}),
]


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("case", BOTH_SIDES, ids=["2-abnormal", "3-abnormal", "6-abnormal"])
def test_abnormal_above_and_below_lists_the_devices_only_and_shortens_in_order(lang, case):
    ui_i18n.set_language(lang)
    args, forms = case[:4], case[4][lang]
    full, first, count = forms
    assert list_hint_text(*args, lambda s: True) == full
    for expected in forms:
        assert list_hint_text(*args, lambda s: s == expected) == expected
    assert list_hint_text(*args, lambda s: False) == count  # never cut: the shortest form
    for text in forms:
        assert not any(word in text for word in DIRECTION_WORDS), text


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_only_the_abnormal_side_is_shown_when_the_other_side_is_normal(lang):
    ui_i18n.set_language(lang)
    t = ui_i18n.t
    # Normal rows hidden above, an abnormal row below: the hint is the "below" hint alone (red beats grey counts).
    below = list_hint_text(["DFU-01", "DFU-02"], ["BT-01", "BT-02"], [], ["BT-02"], lambda s: True)
    assert below == t("list_more_below", n=2) + t("list_abnormal", k=1, ids="BT-02")
    assert "▲" not in below
    above = list_hint_text(["DFU-01"], ["BT-01", "BT-02"], ["DFU-01"], [], lambda s: True)
    assert above == t("list_more_above", n=1) + t("list_abnormal", k=1, ids="DFU-01")
    assert "▼" not in above


def test_list_shows_both_sides_in_red_with_a_tick_on_each(ui):
    show(ui, rows(20, frozen=("DFU-01", "BT-08")))
    wheel(ui, ui._station_widgets["DFU-05"]["card"], -240)
    assert view_top(ui) == 120
    assert "DFU-01" in ui._list_above and "BT-08" in ui._list_below
    assert ui.lbl_list_hint.cget("text") == "DFU-01, BT-08 abnormal"  # devices only, no direction (2026-10-26)
    assert ui.lbl_list_hint.cget("fg") == RED
    ticks = ui.station_track.find_withtag("tick")
    assert len(ticks) == 2
    ys = sorted(ui.station_track.coords(tick)[1] for tick in ticks)
    assert ys[0] < 728 / 2 < ys[1]
    ui.on_toggle_language()
    assert ui.lbl_list_hint.cget("text") == "DFU-01、BT-08 異常"
    assert ui.lbl_list_hint.cget("fg") == RED



@pytest.mark.parametrize("ui", [4 / 3, 5 / 3, 2.0, 8 / 3], indirect=True, ids=["100%", "125%", "150%", "200%"])
def test_many_abnormal_on_both_sides_fit_the_hint_line_without_direction(ui):
    frozen = ("DFU-01", "DFU-02", "DFU-03", "BT-06", "BT-07", "BT-08")
    show(ui, rows(20, frozen=frozen))
    wheel(ui, ui._station_widgets["DFU-05"]["card"], -240)
    assert view_top(ui) == 120
    for lang in ("en", "zh"):
        if ui_i18n.current_language() != lang:
            ui.on_toggle_language()
        text = ui.lbl_list_hint.cget("text")
        assert ui.lbl_list_hint.cget("fg") == RED
        assert not any(word in text for word in DIRECTION_WORDS), text
        assert ui._list_hint_font.measure(text) <= ui_app.LIST_HINT_MAX_W, text  # 188 px, one line
        ui.update_idletasks()
        hint = ui.lbl_list_hint
        if ui.winfo_reqwidth() > ui.winfo_screenwidth() or ui.winfo_reqheight() > ui.winfo_screenheight():
            pytest.skip(f"requested {ui.winfo_reqwidth()}x{ui.winfo_reqheight()}, "
                        f"screen {ui.winfo_screenwidth()}x{ui.winfo_screenheight()}: "
                        "the OS clamps the window to the screen, so rows are squeezed; not a layout result")
        assert hint.winfo_reqwidth() <= hint.winfo_width() and hint.winfo_reqheight() <= hint.winfo_height(), text
        ids = [d for d in frozen if d in text]
        assert ids and text.startswith(ids[0]) or text in ("6 abnormal", "6 台異常"), text
    ys = [ui.station_track.coords(tick)[1] for tick in ui.station_track.find_withtag("tick")]
    assert any(y < 728 / 2 for y in ys) and any(y > 728 / 2 for y in ys), ys  # red ticks on both sides stay
    if ui_i18n.current_language() != "en":
        ui.on_toggle_language()


@pytest.mark.parametrize("ui", [4 / 3, 5 / 3, 2.0, 8 / 3], indirect=True, ids=["100%", "125%", "150%", "200%"])
def test_station_panel_is_as_tall_as_tk_needs_for_title_list_and_hint(ui):
    """The panel has a fixed size (pack_propagate off). If it is shorter than
    its title, the 728 px list and the hint line, pack squeezes the hint line
    (15 of 18 px at 150% with Noto Sans CJK, #139 review r4). Ask Tk itself
    what the panel needs, so this holds on any screen, also when the OS clamps
    the window (Windows CI).
    """
    show(ui, rows(20, frozen=("DFU-01", "BT-08")))
    for lang in ("en", "zh"):
        if ui_i18n.current_language() != lang:
            ui.on_toggle_language()
        panel = ui.station_panel
        configured = int(str(panel.cget("height")))
        panel.pack_propagate(True)
        ui.update_idletasks()
        needed = panel.winfo_reqheight()
        panel.pack_propagate(False)
        panel.configure(height=configured)
        ui.update_idletasks()
        assert needed <= configured, (lang, needed, configured)
    if ui_i18n.current_language() != "en":
        ui.on_toggle_language()


def grouped(groups, per_group):
    """Twelve-ish devices in ``groups`` station groups, e.g. A-01, A-02, B-01, ..."""
    names = ["DFU", "FCT", "BT", "ICT", "AOI", "RUN", "PKG", "OQC"][:groups]
    ids = [f"{name}-{i:02d}" for name in names for i in range(1, per_group[names.index(name)] + 1)]
    return [{"device": d, "station": d.split("-")[0], "status": "IDLE"} for d in ids]


def row_gaps(ui):
    tops = [ui._list_row_tops[d] for d in ui._station_widgets if d in ui._list_row_tops]
    return [b - a - 56 for a, b in zip(tops, tops[1:])]


@pytest.mark.parametrize("groups, per_group", [(5, [3, 3, 2, 2, 2]), (6, [2] * 6), (12, [1] * 8)])
def test_more_than_four_station_groups_drop_the_extra_gap_so_twelve_never_scroll(ui, groups, per_group):
    stations = grouped(min(groups, 8), per_group)
    if groups == 12:  # twelve single-device groups
        stations = [{"device": f"S{i:02d}-01", "station": f"S{i:02d}", "status": "IDLE"} for i in range(1, 13)]
    assert len(stations) == 12
    show(ui, stations)
    assert set(row_gaps(ui)) == {4}
    last = stations[-1]["device"]
    assert ui._list_row_tops[last] + 56 <= 728  # the last row is fully visible
    assert ui._list_content_h <= 728
    assert not ui.station_track.find_withtag("thumb")
    assert ui._list_below == [] and ui.lbl_list_hint.cget("text") == ""
    wheel(ui, ui._station_widgets[stations[0]["device"]]["card"], -120)
    assert view_top(ui) == 0


@pytest.mark.parametrize("groups, per_group", [(3, [10, 1, 1]), (4, [6, 2, 2, 2])])
def test_four_or_fewer_station_groups_keep_the_extra_gap(ui, groups, per_group):
    stations = grouped(groups, per_group)
    show(ui, stations)
    assert row_gaps(ui).count(8) == groups - 1 and row_gaps(ui).count(4) == 12 - groups
    assert ui._list_content_h <= 728
    assert ui._list_row_tops[stations[-1]["device"]] + 56 <= 728
    assert not ui.station_track.find_withtag("thumb")


def test_more_than_four_groups_with_more_than_twelve_devices_also_use_4px(ui):
    stations = grouped(5, [6, 2, 2, 2, 2])
    show(ui, stations)
    assert len(stations) == 14
    assert set(row_gaps(ui)) == {4}
    assert ui._list_content_h == 14 * 56 + 13 * 4  # all rows drawn, 4 px apart, none above the first
    assert ui.station_track.find_withtag("thumb")


def test_thumb_never_shrinks_below_40px_and_still_reaches_both_ends(ui):
    show(ui, rows(250, frozen=()))
    x0, y0, x1, y1 = ui.station_track.coords("thumb")
    assert (y0, y1 - y0) == (0, 40)
    ui.station_canvas.yview_moveto(1.0)
    ui.update()
    x0, y0, x1, y1 = ui.station_track.coords("thumb")
    assert (y1 - y0, y1) == (40, 728)
