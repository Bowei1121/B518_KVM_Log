"""BilingualFormatter: every Log line reads ``HH:MM:SS | EN | 中文`` (issue #106)."""

from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

_HOST_APP_DIR = Path(__file__).resolve().parents[1] / "host-app"
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from bilingual_log import BilingualFormatter, format_bilingual_line  # noqa: E402

_WHEN = dt.datetime(2026, 10, 21, 9, 5, 7)


def _record(msg, args=(), zh=None, exc_info=None):
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, exc_info)
    record.created = _WHEN.timestamp()
    if zh is not None:
        record.zh = zh
    return record


def test_renders_english_and_chinese_columns():
    line = BilingualFormatter().format(_record("Connected to %s", ("kvm-1",), zh="已連線到 %s"))

    assert line == "09:05:07 | Connected to kvm-1 | 已連線到 kvm-1"


def test_chinese_template_takes_the_same_args():
    line = BilingualFormatter().format(_record("Slot %d of %d: %s", (3, 4, "DFU-03"), zh="第 %d 個（共 %d）: %s"))

    assert line == "09:05:07 | Slot 3 of 4: DFU-03 | 第 3 個（共 4）: DFU-03"


def test_without_zh_chinese_column_repeats_english():
    line = BilingualFormatter().format(_record("plain text"))

    assert line == "09:05:07 | plain text | plain text"


def test_exception_traceback_follows_the_line():
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    text = BilingualFormatter().format(_record("Failed", zh="失敗", exc_info=exc_info))

    assert text.startswith("09:05:07 | Failed | 失敗\nTraceback (most recent call last):")
    assert text.rstrip().endswith("ValueError: boom")


def test_format_bilingual_line_falls_back_to_chinese_when_english_is_empty():
    assert format_bilingual_line(_WHEN, "", "中文") == "09:05:07 | 中文 | 中文"


def test_mismatched_chinese_template_keeps_the_line_with_the_english_column():
    # A Chinese template that does not fit its args must not drop the line (review of #109).
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "bad %s", ("x",), None)
    record.zh = "壞 %s %s"
    line = BilingualFormatter().format(record)
    assert line.endswith(" | bad x | bad x")
