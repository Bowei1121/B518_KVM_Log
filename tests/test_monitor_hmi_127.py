"""Red alarm banner under the top bar (issue #127, spec v5.3 §13, §14.4, §14.10 (c)(e), §14.12)."""
import gc
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import alarm_banner  # noqa: E402
import ui_i18n  # noqa: E402
from alarm_banner import banner_lines  # noqa: E402

GAP = "　"  # full-width space between categories / prompt and hints
RED = "#B71C1C"
IDS = [f"DFU-{i:02d}" for i in range(1, 11)] + ["FCT-01", "FCT-02", "BT-01", "BT-02"]


def always(_s):
    return True


def never(_s):
    return False


def frozen(*ids):
    return [(d, "frozen") for d in ids]


def limit(n):
    return lambda s: len(s) <= n


# ---------------------------------------------------------------------------------------------------------------
# Banner text (Tk-free)
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang,expected", [
    ("en", ("▲ DFU-05 Frame frozen", "Check JetKVM", "")),
    ("zh", ("▲ DFU-05 畫面凍結", "請檢查 JetKVM", "")),
])
def test_one_device_main_line_and_hint(lang, expected):
    ui_i18n.set_language(lang)
    assert banner_lines(frozen("DFU-05"), [], [], always, always) == expected
    assert banner_lines(frozen("DFU-05"), [], [], limit(8), always)[0] == "▲ DFU-05"  # main line never cut


@pytest.mark.parametrize("lang,expected", [
    ("en", ("2 devices abnormal", "▲ Frame frozen: DFU-05, FCT-01", "▲ Check JetKVM")),
    ("zh", ("2 台設備異常", "▲ 畫面凍結：DFU-05、FCT-01", "▲ 請檢查 JetKVM")),
])
def test_several_devices_main_list_and_hint(lang, expected):
    ui_i18n.set_language(lang)
    assert banner_lines(frozen("DFU-05", "FCT-01"), [], [], always, always) == expected


@pytest.mark.parametrize("lang,ids_only,two,more", [
    ("en", "▲ DFU-01, DFU-02, DFU-03, DFU-04, DFU-05", "▲ DFU-01, DFU-02", "… +3 more"),
    ("zh", "▲ DFU-01、DFU-02、DFU-03、DFU-04、DFU-05", "▲ DFU-01、DFU-02", "…另 3 台"),
])
def test_list_shortens_to_ids_then_drops_ids_with_a_count(lang, ids_only, two, more):
    ui_i18n.set_language(lang)
    abnormal = frozen(*IDS[:5])
    full = banner_lines(abnormal, [], [], always, always)[1]
    assert len(full) > len(ids_only)
    assert banner_lines(abnormal, [], [], always, limit(len(ids_only)))[1] == ids_only
    assert banner_lines(abnormal, [], [], always, limit(len(two + GAP + more) + 1))[1] == two + GAP + more
    assert banner_lines(abnormal, [], [], always, never)[1] == ui_i18n.t("alarm_more", n=5)  # nothing fits: shortest


def test_hints_shorten_to_a_count_of_the_rest(monkeypatch):
    monkeypatch.setitem(alarm_banner.KINDS, "conn", ("■", "station_offline", "alarm_hint_frozen"))
    abnormal = [("A-01", "frozen"), ("B-01", "conn")]
    t = ui_i18n.t
    for lang, more in (("en", "… +1 more"), ("zh", "…另 1 項")):
        ui_i18n.set_language(lang)
        hint = f"▲ {t('alarm_hint_frozen')}"
        assert banner_lines(abnormal, [], [], always, always)[2] == hint + GAP + f"■ {t('alarm_hint_frozen')}"
        assert banner_lines(abnormal, [], [], always, limit(len(hint + GAP + more)))[2] == hint + GAP + more
        assert banner_lines(abnormal, [], [], always, never)[2] == hint + GAP + more  # the first hint is never reduced to the count alone


@pytest.mark.parametrize("lang,down,up,both,count", [
    ("en", "▼ BT-02: scroll down to see it", "▲ DFU-05: scroll up to see it",
     "▲▼ DFU-05, BT-02: scroll to see them", "▲▼ 2 abnormal: scroll to see"),
    ("zh", "▼ BT-02：請往下捲動查看", "▲ DFU-05：請往上捲動查看", "▲▼ DFU-05、BT-02：請捲動查看", "▲▼ 2 台：請捲動查看"),
])
def test_scroll_prompt_forms(lang, down, up, both, count):
    ui_i18n.set_language(lang)
    hint = ui_i18n.t("alarm_hint_frozen")
    # one device: the prompt leads the second line
    one = lambda a, b: banner_lines(frozen("X-01"), a, b, always, always)[1]  # noqa: E731
    assert one([], ["BT-02"]) == down + GAP + hint
    assert one(["DFU-05"], []) == up.replace("DFU-05", "DFU-05") + GAP + hint
    # several devices: the prompt leads the third line, the category hint follows
    two = frozen("DFU-05", "BT-02")
    third = lambda a, b, fits=always: banner_lines(two, a, b, always, fits)[2]  # noqa: E731
    assert third([], ["BT-02"]) == down + GAP + "▲ " + hint
    full_both = up + GAP + down
    assert third(["DFU-05"], ["BT-02"]) == full_both + GAP + "▲ " + hint
    tail = GAP + "▲ " + hint
    # the hint stays whole; the scroll prompt gives way first: full -> "▲▼ ids" -> "▲▼ N"
    assert third(["DFU-05"], ["BT-02"], limit(len(full_both + tail))) == full_both + tail
    assert third(["DFU-05"], ["BT-02"], limit(len(full_both + tail) - 1)) == both + tail
    assert third(["DFU-05"], ["BT-02"], limit(len(both + tail) - 1)) == count + tail
    short = ui_i18n.t("alarm_scroll_short", a=1, b=1)
    assert third(["DFU-05"], ["BT-02"], never) == short + tail  # the shortest form, never cut


def test_scroll_prompt_for_several_ids_on_one_side():
    ui_i18n.set_language("en")
    line = banner_lines(frozen("BT-01", "BT-02"), [], ["BT-01", "BT-02"], always, always)[2]
    assert line.startswith("▼ BT-01, BT-02: scroll down to see them")
    line = banner_lines(frozen("BT-01", "BT-02"), ["BT-01", "BT-02"], [], always, always)[2]
    assert line.startswith("▲ BT-01, BT-02: scroll up to see them")


def test_hint_shortens_after_the_scroll_prompt_has_priority():
    ui_i18n.set_language("en")
    prompt = "▼ BT-02: scroll down to see it"
    # the hint is kept whole; the one-side prompt shortens first (designer rule, review item 1)
    assert banner_lines(frozen("BT-02"), [], ["BT-02"], always, limit(len(prompt) + 13))[1] == prompt + GAP + "Check JetKVM"
    assert banner_lines(frozen("BT-02"), [], ["BT-02"], always, limit(len(prompt) + 12))[1] == (
        "▼ 1 more below: scroll down" + GAP + "Check JetKVM")
    ui_i18n.set_language("zh")
    prompt = "▼ BT-02：請往下捲動查看"
    assert banner_lines(frozen("BT-02"), [], ["BT-02"], always, limit(len(prompt) + 11))[1] == prompt + GAP + "請檢查 JetKVM"
    assert banner_lines(frozen("BT-02"), [], ["BT-02"], always, limit(len(prompt) + 8))[1] == "▼ 下方 1 台異常" + GAP + "請檢查 JetKVM"


def _two_way(lang):
    ui_i18n.set_language(lang)
    t = ui_i18n.t
    above, below = ["DFU-01", "DFU-02"], ["BT-02", "BT-03"]
    ids = "、" if lang == "zh" else ", "
    full = t("alarm_up_many", ids=ids.join(above)) + GAP + t("alarm_down_many", ids=ids.join(below))
    both = t("alarm_scroll_both", ids=ids.join(above + below))
    count = t("alarm_scroll_count", n=4)
    return above, below, full, both, count, "▲ " + t("alarm_hint_frozen")


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_hint_stays_whole_while_the_scroll_prompt_shortens_first(lang):
    above, below, full, both, count, hint = _two_way(lang)
    four = frozen("DFU-01", "DFU-02", "BT-02", "BT-03")
    third = lambda fits: banner_lines(four, above, below, always, fits)[2]  # noqa: E731
    assert third(always) == full + GAP + hint
    assert third(limit(len(full + GAP + hint))) == full + GAP + hint
    assert third(limit(len(full + GAP + hint) - 1)) == both + GAP + hint  # the "▲▼ ids" form + hint still fits
    assert third(limit(len(both + GAP + hint) - 1)) == count + GAP + hint
    assert third(never) == ui_i18n.t("alarm_scroll_short", a=2, b=2) + GAP + hint


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_never_fits_gives_the_shortest_prompt_and_the_whole_first_hint(lang):
    ui_i18n.set_language(lang)
    short = ui_i18n.t("alarm_scroll_short", a=1, b=1)
    hint = "▲ " + ui_i18n.t("alarm_hint_frozen")
    two = frozen("DFU-05", "BT-02")
    assert banner_lines(two, ["DFU-05"], ["BT-02"], always, never)[2] == short + GAP + hint
    if lang == "zh":
        assert short + GAP + hint == "▲ 上方 1 台　▼ 下方 1 台　▲ 請檢查 JetKVM"


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_hints_shorten_only_after_every_scroll_prompt_failed_and_keep_the_first_whole(lang, monkeypatch):
    monkeypatch.setitem(alarm_banner.KINDS, "conn", ("■", "station_offline", "alarm_hint_frozen"))
    ui_i18n.set_language(lang)
    t = ui_i18n.t
    prompts = [t("alarm_up_one", ids="A-01") + GAP + t("alarm_down_one", ids="B-01"),
               t("alarm_scroll_both", ids=t("sep_list").join(["A-01", "B-01"])),
               t("alarm_scroll_count", n=2), t("alarm_scroll_short", a=1, b=1)]
    hint = "▲ " + t("alarm_hint_frozen")
    more = t("alarm_hint_more", n=1)
    abnormal = [("A-01", "frozen"), ("B-01", "conn")]
    third = lambda fits: banner_lines(abnormal, ["A-01"], ["B-01"], always, fits)[2]  # noqa: E731
    both_hints = hint + GAP + f"■ {t('alarm_hint_frozen')}"
    shortest_whole = min(len(p + GAP + both_hints) for p in prompts)
    # every prompt is tried with all hints whole before any hint shrinks
    assert third(limit(shortest_whole)).endswith(GAP + both_hints)
    shrunk = third(limit(shortest_whole - 1))
    assert shrunk.endswith(GAP + hint + GAP + more) and shrunk[:-len(GAP + hint + GAP + more)] in prompts
    assert third(never) == prompts[-1] + GAP + hint + GAP + more
    # no scroll prompt: the first hint is likewise kept whole
    assert banner_lines(abnormal, [], [], always, never)[2] == hint + GAP + more


# --- one side / both sides: the scroll prompt shrinks in four steps while the hint stays whole (review item 1) ------
def _one_side_variants(side, ids):
    """Variants 1-4 for one side, written out from the designer's rule (side: 'up' = above, 'down' = below)."""
    t, sep, n = ui_i18n.t, ui_i18n.t("sep_list"), len(ids)
    arrow = "▲" if side == "up" else "▼"
    out = [t(f"alarm_{side}_{'one' if n == 1 else 'many'}", ids=sep.join(ids))]
    for k in range(n - 1, 0, -1):
        kept = ids[:k] if side == "down" else ids[-k:]  # keep those nearest the visible area, in list order
        out.append(t(f"alarm_{side}_many", ids=sep.join(kept) + t("alarm_more", n=n - k)))
    out += [t(f"alarm_{side}_count", n=n), t(f"alarm_{side}_short", n=n)]
    assert all(v.startswith(arrow) for v in out)
    return out


def _one_side_third(side, ids, fits):
    ids12 = [f"DFU-{i:02d}" for i in range(1, 13)]
    above, below = (ids, []) if side == "up" else ([], ids)
    return banner_lines(frozen(*ids12), above, below, always, fits)[2]


DOWN8 = [f"DFU-{i:02d}" for i in range(5, 13)]
UP8 = [f"DFU-{i:02d}" for i in range(1, 9)]


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("side,ids", [("down", DOWN8), ("up", UP8)])
def test_one_side_prompt_steps_full_then_first_few_and_count_then_count_then_short(lang, side, ids):
    ui_i18n.set_language(lang)
    hint = "▲ " + ui_i18n.t("alarm_hint_frozen")
    variants = _one_side_variants(side, ids)
    assert len(variants) == 1 + 7 + 2
    lengths = [len(v + GAP + hint) for v in variants]
    reached = 0
    for variant, length in zip(variants, lengths):  # the first variant (in rule order) that fits is chosen
        first = next(v for v in variants if len(v + GAP + hint) <= length)
        assert _one_side_third(side, ids, limit(length)) == first + GAP + hint
        reached += first == variant
    assert reached >= 1 + 3 + 2  # full, several first-few variants, count and short are all hit exactly
    assert _one_side_third(side, ids, never) == variants[-1] + GAP + hint  # never fits: the shortest + whole hint
    assert _one_side_third(side, ids, always) == variants[0] + GAP + hint


@pytest.mark.parametrize("side,ids", [("down", DOWN8), ("up", UP8)])
def test_one_side_zh_exact_strings(side, ids):
    ui_i18n.set_language("zh")
    hint = GAP + "▲ 請檢查 JetKVM"
    if side == "down":
        expected = ["▼ DFU-05、DFU-06、DFU-07、DFU-08、DFU-09、DFU-10、DFU-11、DFU-12：請往下捲動查看",
                    "▼ DFU-05、DFU-06…另 6 台：請往下捲動查看", "▼ DFU-05…另 7 台：請往下捲動查看",
                    "▼ 下方 8 台異常：請往下捲動查看", "▼ 下方 8 台異常"]
    else:
        expected = ["▲ DFU-01、DFU-02、DFU-03、DFU-04、DFU-05、DFU-06、DFU-07、DFU-08：請往上捲動查看",
                    "▲ DFU-07、DFU-08…另 6 台：請往上捲動查看", "▲ DFU-08…另 7 台：請往上捲動查看",
                    "▲ 上方 8 台異常：請往上捲動查看", "▲ 上方 8 台異常"]
    got = _one_side_variants(side, ids)
    assert [got[0], got[6], got[7], got[8], got[9]] == expected
    assert _one_side_third(side, ids, limit(len(expected[1] + hint))) == expected[1] + hint
    assert _one_side_third(side, ids, limit(len(expected[3] + hint))) == expected[3] + hint
    assert _one_side_third(side, ids, never) == expected[4] + hint


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("side", ["up", "down"])
def test_single_off_screen_device_skips_the_first_few_variant(lang, side):
    ui_i18n.set_language(lang)
    hint = "▲ " + ui_i18n.t("alarm_hint_frozen")
    variants = _one_side_variants(side, ["DFU-05"])
    assert len(variants) == 3  # full, count, short
    lengths = [len(v + GAP + hint) for v in variants]
    for length in (lengths[1], lengths[2], lengths[1] - 1, lengths[2] - 1):  # first fit in rule order: 1 -> 3 -> 4
        first = next((v for v in variants if len(v + GAP + hint) <= length), variants[-1])
        assert _one_side_third(side, ["DFU-05"], limit(length)) == first + GAP + hint
    assert _one_side_third(side, ["DFU-05"], limit(lengths[2])) in (variants[0] + GAP + hint, variants[2] + GAP + hint)
    assert _one_side_third(side, ["DFU-05"], never) == variants[2] + GAP + hint


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_both_sides_reach_the_short_form_with_the_whole_hint(lang):
    ui_i18n.set_language(lang)
    t = ui_i18n.t
    sep = t("sep_list")
    above, below = ["DFU-01", "DFU-02"], [f"DFU-{i:02d}" for i in range(7, 13)]
    hint = "▲ " + t("alarm_hint_frozen")
    full = t("alarm_up_many", ids=sep.join(above)) + GAP + t("alarm_down_many", ids=sep.join(below))
    both = t("alarm_scroll_both", ids=sep.join(above + below))
    count = t("alarm_scroll_count", n=8)
    short = t("alarm_scroll_short", a=2, b=6)
    ids12 = [f"DFU-{i:02d}" for i in range(1, 13)]
    third = lambda fits: banner_lines(frozen(*ids12), above, below, always, fits)[2]  # noqa: E731
    candidates = [full, both, count, short]
    for c in candidates:  # the first variant (in rule order) that fits is chosen
        length = len(c + GAP + hint)
        first = next(v for v in candidates if len(v + GAP + hint) <= length)
        assert third(limit(length)) == first + GAP + hint
    assert third(limit(len(short + GAP + hint))) in (count + GAP + hint, short + GAP + hint)  # reaches the 4th form
    assert third(never) == short + GAP + hint
    assert short == ("▲ 上方 2 台　▼ 下方 6 台" if lang == "zh" else "▲ 2 above　▼ 6 below")


def test_no_abnormal_gives_no_lines():
    assert banner_lines([], [], [], always, always) == ("", "", "")


def heartbeat(*ids):
    return [(d, "heartbeat") for d in ids]


@pytest.mark.parametrize("lang,expected", [
    ("en", ("♥ DFU-03 Heartbeat stopped", "Check test PC and Log Solution", "")),
    ("zh", ("♥ DFU-03 心跳停止", "請檢查測試電腦和 Log Solution", "")),
])
def test_one_heartbeat_device_main_line_and_hint(lang, expected):
    ui_i18n.set_language(lang)
    assert banner_lines(heartbeat("DFU-03"), [], [], always, always) == expected


@pytest.mark.parametrize("lang,expected", [
    ("en", ("2 devices abnormal", "♥ Heartbeat stopped: DFU-04" + GAP + "▲ Frame frozen: DFU-05",
            "♥ Check test PC and Log Solution" + GAP + "▲ Check JetKVM")),
    ("zh", ("2 台設備異常", "♥ 心跳停止：DFU-04" + GAP + "▲ 畫面凍結：DFU-05",
            "♥ 請檢查測試電腦和 Log Solution" + GAP + "▲ 請檢查 JetKVM")),
])
def test_mixed_heartbeat_and_frozen_are_grouped_in_category_order(lang, expected):
    ui_i18n.set_language(lang)
    mixed = frozen("DFU-05") + heartbeat("DFU-04")  # frozen listed first on purpose
    assert banner_lines(mixed, [], [], always, always) == expected


def test_heartbeat_sorts_before_frozen():
    mixed = [("A", "frozen"), ("B", "heartbeat"), ("C", "frozen"), ("D", "heartbeat")]
    assert alarm_banner.sort_abnormal(mixed) == [("B", "heartbeat"), ("D", "heartbeat"),
                                                 ("A", "frozen"), ("C", "frozen")]


# ---------------------------------------------------------------------------------------------------------------
# Tk banner
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
    old = _probe.tk.call("tk", "scaling")

    def apply(root):
        root.wm_maxsize(10000, 10000)
        root.tk.call("tk", "scaling", 4 / 3)
        return 4 / 3

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


def rows(frozen_ids=(), n=14):
    return [{"device": d, "station": d.split("-")[0], "status": "FROZEN" if d in frozen_ids else "IDLE"}
            for d in IDS[:n]]


def show(ui, frozen_ids=(), n=14):
    ui._update_status_ui({"status": "ok", "stations": rows(frozen_ids, n)})
    ui.geometry("960x1010")
    ui.update()


def labels(ui):
    return [ui.lbl_alarm_main.cget("text"), ui.lbl_alarm_l2.cget("text"), ui.lbl_alarm_l3.cget("text")]


def font_of(ui, label):
    name = label.cget("font")
    return int(ui.tk.call("font", "configure", name, "-size")), ui.tk.call("font", "configure", name, "-weight")


def test_banner_pushes_the_body_down_by_exactly_120_and_back(ui):
    show(ui)
    assert not ui.alarm_banner.winfo_ismapped()
    y0 = ui.body.winfo_y()
    show(ui, ("BT-02",))
    assert ui.alarm_banner.winfo_ismapped()
    assert ui.alarm_banner.winfo_height() == 120
    assert ui.alarm_banner.winfo_width() == ui.winfo_width()
    assert ui.alarm_banner.winfo_y() == ui.top_frame.winfo_y() + ui.top_frame.winfo_height() + 6
    assert ui.body.winfo_y() == y0 + 120
    assert ui.alarm_banner.cget("bg") == RED
    show(ui, ())
    assert not ui.alarm_banner.winfo_ismapped()
    assert ui.alarm_banner.winfo_manager() == ""  # pack_forget: no blank space
    assert ui.body.winfo_y() == y0


def test_banner_fonts_and_colours(ui):
    show(ui, ("BT-02",))
    assert font_of(ui, ui.lbl_alarm_main) == (-40, "bold")
    assert font_of(ui, ui.lbl_alarm_l2) == (-20, "bold")
    assert font_of(ui, ui.lbl_alarm_l3) == (-20, "bold")
    for label in (ui.lbl_alarm_main, ui.lbl_alarm_l2, ui.lbl_alarm_l3):
        assert label.cget("fg") == "#ffffff" and label.cget("bg") == RED
    assert (int(ui.alarm_icon.cget("width")), int(ui.alarm_icon.cget("height"))) == (72, 72)


def test_single_device_banner_text_and_no_third_line(ui):
    show(ui, ("DFU-05",))
    assert labels(ui)[0] == "▲ DFU-05 Frame frozen"
    assert labels(ui)[1] == "Check JetKVM"
    assert labels(ui)[2] == "" and not ui.lbl_alarm_l3.winfo_ismapped()
    ui.on_toggle_language()
    assert labels(ui)[:2] == ["▲ DFU-05 畫面凍結", "請檢查 JetKVM"]


def test_off_screen_devices_count_and_the_scroll_prompt_follows_the_list(ui):
    show(ui, ("DFU-05", "BT-02"))
    assert ui._list_below == ["BT-01", "BT-02"]
    assert labels(ui)[0] == "2 devices abnormal"  # the off-screen BT-02 still counts
    assert "BT-02" in labels(ui)[1]
    assert labels(ui)[2].startswith("▼ BT-02: scroll down to see it")
    ui.station_canvas.yview_moveto(1.0)
    ui.update()
    assert labels(ui)[0] == "2 devices abnormal"
    assert labels(ui)[2] == "▲ Check JetKVM"  # both visible: no scroll prompt


def test_abnormal_above_and_below_gives_the_two_way_prompt(ui):
    show(ui, ("DFU-01", "BT-02"))
    ui.station_canvas.yview_scroll(1, "units")
    ui.update()
    assert ui._list_above == ["DFU-01"] and ui._list_below == ["BT-02"]
    third = labels(ui)[2]
    assert third == "▲ DFU-01: scroll up to see it" + GAP + "▼ BT-02: scroll down to see it" + GAP + "▲ Check JetKVM"
    assert ui._banner_line_font.measure(third) <= ui._banner_text_width(1)
    ui.station_canvas.yview_moveto(0)
    ui.update()
    assert labels(ui)[2].startswith("▼ BT-02: scroll down to see it")


def test_same_abnormal_set_does_not_redraw(ui):
    show(ui, ("BT-02",))
    n = ui._banner_redraws
    assert n >= 1
    show(ui, ("BT-02",))
    show(ui, ("BT-02",))
    assert ui._banner_redraws == n
    show(ui, ("BT-02", "FCT-01"))
    assert ui._banner_redraws == n + 1


def test_frozen_rows_show_a_triangle_instead_of_the_round_light(ui):
    show(ui, ("BT-02",))
    frozen, normal = ui._station_widgets["BT-02"], ui._station_widgets["DFU-01"]
    def on_top(widgets):  # the ▲ covers the light when it is later in the stacking order
        order = widgets["card"].winfo_children()
        return order.index(widgets["icon"]) > order.index(widgets["light"])

    assert on_top(frozen) and not on_top(normal)
    ui.station_canvas.yview_moveto(1.0)
    ui.update()
    icon = frozen["icon"]
    assert (icon.winfo_width(), icon.winfo_height()) == (16, 16)
    assert (icon.winfo_x(), icon.winfo_y()) == (12, 7)
    assert "polygon" in {icon.type(i) for i in icon.find_all()}
    assert any(icon.itemcget(i, "text") == "!" for i in icon.find_all() if icon.type(i) == "text")
    show(ui, ())
    assert not on_top(frozen)  # back to the round light


def test_clicking_the_banner_selects_the_first_abnormal_device_without_scrolling(ui):
    show(ui, ("BT-01", "BT-02"))
    assert ui._list_below == ["BT-01", "BT-02"]
    pos = ui.station_canvas.yview()
    for widget in (ui.alarm_banner, ui.lbl_alarm_main, ui.alarm_icon):
        ui.var_sel_device.set("DFU-01")
        widget.event_generate("<Button-1>", x=5, y=5)
        ui.update()
        assert ui.var_sel_device.get() == "BT-01"
        assert ui.station_canvas.yview() == pos


def test_banner_logs_once_per_event(ui, monkeypatch):
    calls = []
    original = ui._append_log

    def record(en, zh="", level="info"):
        calls.append((en, zh))
        return original(en, zh, level)

    monkeypatch.setattr(ui, "_append_log", record)

    def lines():
        return [c for c in calls if "larm banner" in c[0]]

    show(ui, ("BT-02",))
    assert lines() == [("Alarm banner shown (1 abnormal)", "顯示警示橫幅（1 台異常）")]
    show(ui, ("BT-02",))
    show(ui, ("BT-02",))
    assert len(lines()) == 1
    show(ui, ("BT-01",))  # same count, another device: no line
    assert len(lines()) == 1
    show(ui, ("BT-01", "BT-02"))
    assert lines()[-1] == ("Alarm banner shown (2 abnormal)", "顯示警示橫幅（2 台異常）") and len(lines()) == 2
    show(ui, ())
    assert lines()[-1] == ("All abnormal states cleared; alarm banner hidden", "所有異常已解除，警示橫幅已隱藏")
    assert len(lines()) == 3
    show(ui, ())
    assert len(lines()) == 3


def test_core_disconnect_hides_the_banner(ui):
    show(ui, ("BT-02",))
    assert ui.alarm_banner.winfo_ismapped()
    ui._update_status_ui({"status": "error"})
    ui.update()
    assert not ui.alarm_banner.winfo_ismapped()


def test_two_way_prompt_with_real_font_width_keeps_the_hint_whole(ui):
    ui_i18n.set_language("zh")
    font = ui._banner_line_font
    above, below, full, both, count, hint = _two_way("zh")
    four = frozen("DFU-01", "DFU-02", "BT-02", "BT-03")
    fits = lambda s: font.measure(s) <= 818  # noqa: E731
    third = banner_lines(four, above, below, fits, fits)[2]
    candidates = [full + GAP + hint, both + GAP + hint, count + GAP + hint]
    assert third == next((c for c in candidates if fits(c)), candidates[-1])
    assert third.endswith(GAP + hint) and "…另" not in third
    assert third == both + GAP + hint


def _banner_log_lines(ui, monkeypatch):
    calls = []
    original = ui._append_log

    def record(en, zh="", level="info"):
        calls.append((en, zh))
        return original(en, zh, level)

    monkeypatch.setattr(ui, "_append_log", record)
    return lambda: [c for c in calls if "larm banner" in c[0]]


SHOWN_1 = ("Alarm banner shown (1 abnormal)", "顯示警示橫幅（1 台異常）")
CLEARED = ("All abnormal states cleared; alarm banner hidden", "所有異常已解除，警示橫幅已隱藏")


def _core_down(ui):
    ui._update_status_ui({"status": "error"})
    ui.update()


def test_core_disconnect_and_reconnect_do_not_log_cleared_or_shown_again(ui, monkeypatch):
    lines = _banner_log_lines(ui, monkeypatch)
    show(ui, ("BT-02",))
    assert lines() == [SHOWN_1]
    _core_down(ui)
    assert not ui.alarm_banner.winfo_ismapped()  # cannot see status: hidden, but not "cleared"
    assert lines() == [SHOWN_1]
    _core_down(ui)
    assert lines() == [SHOWN_1]
    show(ui, ("BT-02",))  # Core is back and BT-02 is still frozen
    assert ui.alarm_banner.winfo_ismapped()
    assert lines() == [SHOWN_1]


def test_core_reconnect_with_nothing_frozen_logs_cleared_once(ui, monkeypatch):
    lines = _banner_log_lines(ui, monkeypatch)
    show(ui, ("BT-02",))
    _core_down(ui)
    show(ui, ())
    assert lines() == [SHOWN_1, CLEARED]
    show(ui, ())
    assert lines() == [SHOWN_1, CLEARED]


def test_core_reconnect_with_a_different_count_logs_shown(ui, monkeypatch):
    lines = _banner_log_lines(ui, monkeypatch)
    show(ui, ("BT-02",))
    _core_down(ui)
    show(ui, ("BT-01", "BT-02"))
    assert lines() == [SHOWN_1, ("Alarm banner shown (2 abnormal)", "顯示警示橫幅（2 台異常）")]


def test_count_decreasing_logs_shown_with_the_new_count(ui, monkeypatch):
    lines = _banner_log_lines(ui, monkeypatch)
    show(ui, ("BT-01", "BT-02"))
    show(ui, ("BT-02",))
    assert lines()[-1] == SHOWN_1 and len(lines()) == 2


def _is_red(colour):
    if not isinstance(colour, str) or not colour.startswith("#") or len(colour) != 7:
        return False
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return r >= 0xA0 and g <= 0x50 and b <= 0x50


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


COLOUR_OPTIONS = ("background", "foreground", "highlightbackground", "activebackground", "activeforeground",
                  "disabledforeground", "selectbackground", "troughcolor", "insertbackground")


def test_no_red_within_16px_of_the_preview_canvas_and_no_pure_red_anywhere(ui):
    show(ui, ("DFU-01", "BT-02"))
    canvas = ui.canvas_preview
    left, top = canvas.winfo_rootx() - 16, canvas.winfo_rooty() - 16
    right = canvas.winfo_rootx() + canvas.winfo_width() + 16
    bottom = canvas.winfo_rooty() + canvas.winfo_height() + 16
    ancestors = set()
    w = canvas
    while w is not None:
        ancestors.add(str(w))
        w = w.master
    near = []
    for w in _walk(ui):
        if not w.winfo_viewable():
            continue
        opts = w.configure()
        for name in COLOUR_OPTIONS:
            value = opts.get(name, ("",) * 5)[-1]
            assert str(value).lower() not in ("#ff0000", "red"), (w, name)
            if _is_red(value) and str(w) not in ancestors:
                x0, y0 = w.winfo_rootx(), w.winfo_rooty()
                if x0 < right and x0 + w.winfo_width() > left and y0 < bottom and y0 + w.winfo_height() > top:
                    near.append((str(w), name, value))
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                for option in ("fill", "outline"):
                    try:
                        value = w.itemcget(item, option)
                    except tk.TclError:  # e.g. a text item has no outline
                        continue
                    assert value.lower() not in ("#ff0000", "red"), (w, item)
                    if _is_red(value) and str(w) != str(canvas):
                        x0, y0, x1, y1 = w.bbox(item) or (0, 0, 0, 0)
                        rx, ry = w.winfo_rootx(), w.winfo_rooty()
                        if rx + x0 < right and rx + x1 > left and ry + y0 < bottom and ry + y1 > top:
                            near.append((str(w), item, value))
    assert near == []


# --- real banner font (20 px bold): the hint line is never cut and the hint stays whole (review item 1) ------------
def _real_font_cases():
    ids12 = [f"DFU-{i:02d}" for i in range(1, 13)]
    return {"below": ([], ids12[4:]), "above": (ids12[:8], []), "both": (ids12[:2], ids12[6:])}


def _real_font_expected(case, fits):
    t, sep = ui_i18n.t, ui_i18n.t("sep_list")
    above, below = _real_font_cases()[case]
    hint = GAP + "▲ " + t("alarm_hint_frozen")
    if case == "both":
        candidates = [t("alarm_up_many", ids=sep.join(above)) + GAP + t("alarm_down_many", ids=sep.join(below)),
                      t("alarm_scroll_both", ids=sep.join(above + below)), t("alarm_scroll_count", n=8),
                      t("alarm_scroll_short", a=len(above), b=len(below))]
    else:
        side, ids = ("up", above) if above else ("down", below)
        candidates = _one_side_variants(side, ids)
    return next((c for c in candidates if fits(c + hint)), candidates[-1]) + hint


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("case", ["below", "above", "both"])
@pytest.mark.parametrize("width", [940, "banner"])
def test_real_font_hint_line_is_never_cut_and_the_hint_stays_whole(ui, lang, case, width):
    ui_i18n.set_language(lang)
    if width == "banner":
        # the real text width of a one-icon banner in a 960 px wide window (the test window itself may be wider)
        width = 960 - ui_app.BANNER_PAD - ui_app.BANNER_ICON - ui_app.BANNER_GAP - ui_app.BANNER_PAD
        assert width == 818
    font = ui._banner_line_font
    fits = lambda s: font.measure(s) <= width  # noqa: E731
    above, below = _real_font_cases()[case]
    ids12 = [f"DFU-{i:02d}" for i in range(1, 13)]
    line = banner_lines(frozen(*ids12), above, below, fits, fits)[2]
    assert font.measure(line) <= width
    assert line.endswith(GAP + "▲ " + ui_i18n.t("alarm_hint_frozen"))
    assert ui_i18n.t("alarm_hint_more", n=1) not in line
    assert line == _real_font_expected(case, fits)


def test_new_scroll_prompt_strings_exist_in_en_and_zh_with_the_same_placeholders():
    import re
    keys = ["alarm_up_count", "alarm_down_count", "alarm_up_short", "alarm_down_short", "alarm_scroll_short"]
    found = {}
    for lang in ("en", "zh"):
        ui_i18n.set_language(lang)
        for key in keys:
            text = ui_i18n.t(key)
            assert text != key, (lang, key)
            found[lang, key] = set(re.findall(r"\{(\w+)\}", text))
    for key in keys:
        assert found["en", key] == found["zh", key] != set(), key


def show_rows(ui, extra):
    ui._update_status_ui({"status": "ok", "stations": [
        {"device": d, "station": d.split("-")[0], "status": "IDLE", **extra.get(d, {})} for d in IDS[:6]]})
    ui.geometry("960x1010")
    ui.update()


def icon_texts(ui):
    canvas = ui.alarm_icon
    return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]


def test_heartbeat_banner_shows_a_heart_icon_and_text(ui):
    show_rows(ui, {"DFU-03": {"is_heartbeat_stopped": True}})
    assert labels(ui)[:2] == ["♥ DFU-03 Heartbeat stopped", "Check test PC and Log Solution"]
    assert int(ui.alarm_icon.cget("width")) == ui_app.BANNER_ICON
    assert icon_texts(ui) == ["♥"]


def test_heartbeat_and_frozen_draw_two_icons_and_the_text_width_uses_two(ui):
    show_rows(ui, {"DFU-03": {"is_heartbeat_stopped": True}, "DFU-05": {"status": "FROZEN", "is_frozen": True}})
    assert int(ui.alarm_icon.cget("width")) == 2 * ui_app.BANNER_ICON
    canvas = ui.alarm_icon
    assert icon_texts(ui) == ["♥", "!"]  # heart first, then the triangle (its "!")
    assert "polygon" in {canvas.type(i) for i in canvas.find_all()}
    assert ui._banner_text_width(2) == ui._banner_text_width(1) - ui_app.BANNER_ICON


def test_banner_click_selects_the_heartbeat_device_first(ui):
    show_rows(ui, {"DFU-02": {"status": "FROZEN", "is_frozen": True}, "DFU-04": {"is_heartbeat_stopped": True}})
    assert ui._abnormal_devices() == [("DFU-04", "heartbeat"), ("DFU-02", "frozen")]
    ui.var_sel_device.set("DFU-01")
    ui.lbl_alarm_main.event_generate("<Button-1>", x=5, y=5)
    ui.update()
    assert ui.var_sel_device.get() == "DFU-04"
