# -*- coding: utf-8 -*-
"""Bilingual Log lines shared by the Monitor UI log file and the Core Service log (issue #106).

Every line reads ``HH:MM:SS | <EN> | <中文>`` so the RD (English) and the site operators (Chinese)
read the same file. Files are already one per day, so the date is not repeated on each line.

Core Service call sites pass the Chinese half as ``extra={"zh": "..."}``; the Chinese template is
%-formatted with the same args as the English message. A call without ``zh`` repeats the English text
in the Chinese column rather than dropping the line.
"""

from __future__ import annotations

import datetime as _dt
import logging


def format_bilingual_line(when: _dt.datetime, en: str, zh: str) -> str:
    """One line without a trailing newline. An empty ``en`` falls back to ``zh``."""
    return f"{when.strftime('%H:%M:%S')} | {en or zh} | {zh}"


class BilingualFormatter(logging.Formatter):
    """logging formatter that renders the bilingual line; exceptions follow on the next lines."""

    def format(self, record: logging.LogRecord) -> str:
        en = record.getMessage()
        zh_template = getattr(record, "zh", None)
        if zh_template is None:
            zh = en
        else:
            zh = zh_template % record.args if record.args else zh_template
        line = format_bilingual_line(_dt.datetime.fromtimestamp(record.created), en, zh)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line
