import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import alarm_banner
import ui_i18n
import window_placement as placement
from ui_widgets import _width_is_free


def test_banner_labels_are_excluded_from_width_fix():
    class Label:
        _banner_single_line = True
    assert not _width_is_free(Label())


def test_truncation_fits_and_keeps_joined_glyphs():
    assert alarm_banner.truncate("A e\u0301 xyz", lambda s: len(s) <= 4) == "A…"
    assert alarm_banner.truncate("A 👩\u200d💻 xyz", lambda s: len(s) <= 5) == "A…"
    assert alarm_banner.truncate("long", lambda s: False) == ""


def test_connection_failure_copy_and_core_copy():
    for lang, expected in (("zh", ("■✕ DFU-01 連線失敗異常", "檢查 JetKVM 電源與網路線")),
                           ("en", ("■✕ DFU-01 Connection failure", "Check JetKVM power and network cable"))):
        ui_i18n.set_language(lang)
        try:
            assert alarm_banner.banner_lines([("DFU-01", "connect_failed")], [], [], lambda _: True, lambda _: True)[:2] == expected
        finally:
            ui_i18n.set_language("en")
    assert ui_i18n.t("alarm_core_down", "zh") == "Core 未啟動"
    assert ui_i18n.t("alarm_core_down", "en") == "Core not started"
    assert ui_i18n.t("alarm_core_down_hint", "zh") == "請按「重新連線」；仍失敗請通知 TE"


def test_open_geometry_caps_width_and_preserves_top_right():
    area = placement.Rect(0, 0, 1920, 1032)
    frame = placement.Frame(8, 8, 31, 8)
    assert placement.open_geometry(area, frame, (1920, 1080), (1400, 2000)) == "1400x993+504+0"  # never narrower than content (M1)
