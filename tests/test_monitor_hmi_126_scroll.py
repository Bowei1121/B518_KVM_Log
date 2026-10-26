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
def ui(monkeypatch):
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
