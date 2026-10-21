# -*- coding: utf-8 -*-
"""Per-day log file for the Monitor UI Log panel (issue #105).

Each day gets one file, ``<prefix>_YYYY-MM-DD.log``, in the ``logs`` folder that sits beside the
template root (``<template root>/../logs``). Files older than ``RETENTION_DAYS`` are deleted when a
new day's file is opened and on startup. Every line is bilingual: ``HH:MM:SS | EN | 中文`` (issue #106).
"""

from __future__ import annotations

import datetime as _dt
import re
import threading
from pathlib import Path
from typing import Callable, List, Optional, Union

from bilingual_log import format_bilingual_line

RETENTION_DAYS = 30
_NAME_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9_]+)_(?P<date>\d{4}-\d{2}-\d{2})\.log$")


def log_dir_for_template_root(root: Union[str, Path]) -> Path:
    """``<template root>/../logs``: beside the template folder, not inside it."""
    return Path(root).expanduser().parent / "logs"


def prune_old_logs(
    directory: Union[str, Path],
    today: _dt.date,
    prefix: Optional[str] = None,
    keep_days: int = RETENTION_DAYS,
) -> List[Path]:
    """Delete ``<prefix>_YYYY-MM-DD.log`` files at least ``keep_days`` days old. Returns the removed paths."""
    folder = Path(directory)
    removed: List[Path] = []
    if not folder.is_dir():
        return removed
    for path in folder.iterdir():
        match = _NAME_RE.match(path.name)
        if match is None or (prefix is not None and match.group("prefix") != prefix):
            continue
        try:
            file_day = _dt.date.fromisoformat(match.group("date"))
        except ValueError:  # e.g. monitor_ui_2026-02-30.log: not ours to delete, and must not break startup
            continue
        if (today - file_day).days >= keep_days:
            try:
                path.unlink()
            except OSError:  # locked by antivirus or another process; try again on a later day
                continue
            removed.append(path)
    return removed


class DailyLogFile:
    """Append-only writer that switches to a new file when the local date changes."""

    def __init__(
        self,
        directory: Union[str, Path],
        prefix: str,
        clock: Optional[Callable[[], _dt.datetime]] = None,
    ) -> None:
        self.directory = Path(directory)
        self.prefix = prefix
        self._clock = clock or _dt.datetime.now
        self._lock = threading.Lock()
        self._day: Optional[_dt.date] = None
        self._fh = None

    def path_for(self, day: _dt.date) -> Path:
        return self.directory / f"{self.prefix}_{day.isoformat()}.log"

    def write(self, en: str, zh: str = "", when: Optional[_dt.datetime] = None) -> str:
        """Append one bilingual line and return the text that was written (without the newline)."""
        now = when or self._clock()
        line = format_bilingual_line(now, en, zh or en)
        with self._lock:
            if self._fh is None or now.date() != self._day:
                self._open(now.date())
            self._fh.write(line + "\n")
            self._fh.flush()
        return line

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
                self._day = None

    def _open(self, day: _dt.date) -> None:
        if self._fh is not None:
            self._fh.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path_for(day), "a", encoding="utf-8")
        self._day = day
        prune_old_logs(self.directory, day, prefix=self.prefix)
