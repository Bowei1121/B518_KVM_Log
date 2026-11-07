"""Persist and load the most recent JetKVM template matching diagnostics."""

import json
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2


log = logging.getLogger(__name__)

# History archive (JPEG only). Core has no shared settings mechanism for these, so they are module
# constants, overridable per instance through the constructor.
HISTORY_HOURS = 24
HISTORY_MAX_MB = 2048
HISTORY_JPEG_QUALITY = 85


# Archive and cleanup run on one background thread: the Core TCP event loop never waits for JPEG encoding or
# directory scans, and a single worker serializes every write and sweep under the shared archive root.
_HISTORY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="match-diag-history")


def wait_for_history():
    """Block until every queued history archive/cleanup finished (tests and shutdown)."""
    _HISTORY_EXECUTOR.submit(lambda: None).result()


def _bilingual_note(note):
    """Store ``note`` as ``{"en", "zh"}``. A missing zh falls back to en; a plain string is kept in both."""
    if note is None:
        return {"en": "", "zh": ""}
    if isinstance(note, dict):
        en = note.get("en", "")
        return {"en": en, "zh": note.get("zh") or en}
    return {"en": note, "zh": note}


def note_for_language(note, lang):
    """The note text for ``lang`` (``"en"`` or ``"zh"``).

    ``note`` is ``{"en": ..., "zh": ...}`` (an empty or missing language falls back to en) or a legacy plain
    string, returned as-is.
    """
    if isinstance(note, dict):
        return note.get(lang) or note.get("en", "")
    return "" if note is None else note


class MatchDiagnostics:
    """One-run recorder with a small interface for matching flow code."""

    def __init__(self, template_root, device, history_hours=HISTORY_HOURS, history_max_mb=HISTORY_MAX_MB,
                 now=None):
        self.device = str(device).upper()
        self.history_hours = history_hours
        self.history_max_mb = history_max_mb
        self._now = now  # injectable clock (epoch seconds) for tests
        self._root = Path(template_root) / "_captures" / "match_diagnostics"
        self._annotated = {}  # image name -> annotated frame, kept for the history JPEG
        self.directory = self._root / self.device / "latest"
        if self.directory.exists():
            shutil.rmtree(str(self.directory))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.records = []

    @staticmethod
    def latest_directory(template_root, device):
        return Path(template_root) / "_captures" / "match_diagnostics" / str(device).upper() / "latest"

    @classmethod
    def load(cls, template_root, device):
        directory = cls.latest_directory(template_root, device)
        summary = directory / "summary.json"
        if not summary.exists():
            return None
        with summary.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def record(self, key, score, threshold, frame=None, box=None, note=None, color=(0, 200, 255)):
        matched = score is not None and box is not None and score >= threshold
        image_name = ""
        if frame is not None:
            annotated = frame.copy()
            if box is not None:
                x, y, width, height = box
                cv2.rectangle(annotated, (x, y), (x + width, y + height), color, 2)
            score_text = "n/a" if score is None else "{:.3f}".format(score)
            label = "{} {} ({})".format(key, score_text, "MATCH" if matched else "MISS")
            cv2.putText(annotated, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
            image_name = "{:02d}_{}.png".format(len(self.records) + 1, key)
            cv2.imwrite(str(self.directory / image_name), annotated)
            self._annotated[image_name] = annotated
        self.records.append({
            "key": key,
            "score": score,
            "threshold": threshold,
            "matched": matched,
            "box": list(box) if box is not None else None,
            "note": _bilingual_note(note),
            "image": image_name,
        })

    def finalize(self, ok, operation):
        payload = {
            "device": self.device,
            "operation": operation,
            "ok": bool(ok),
            "records": self.records,
        }
        with (self.directory / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        annotated, self._annotated = self._annotated, {}
        snapshot = dict(payload, records=[dict(record) for record in payload["records"]])
        stamp = datetime.fromtimestamp(self._clock())
        _HISTORY_EXECUTOR.submit(self._archive_and_cleanup, snapshot, annotated, ok, operation, stamp)
        return payload

    def _clock(self):
        return time.time() if self._now is None else self._now

    def _archive_and_cleanup(self, payload, annotated, ok, operation, stamp):
        try:
            self._archive(payload, annotated, ok, operation, stamp)
        except Exception:  # archiving is diagnostics only: never fail the flow
            log.exception("match diagnostics history archive failed")
        try:
            self._cleanup_history()
        except Exception:
            log.exception("match diagnostics history cleanup failed")

    def _archive(self, payload, annotated, ok, operation, stamp):
        name = "{}_{:03d}_{}_{}".format(stamp.strftime("%H%M%S"), stamp.microsecond // 1000, operation,
                                        "OK" if ok else "FAIL")
        day = self._root / self.device / "history" / stamp.strftime("%Y%m%d")
        day.mkdir(parents=True, exist_ok=True)
        target = day / name
        suffix = 1
        while True:  # exclusive create: same millisecond/operation/result must not overwrite an earlier run
            try:
                target.mkdir()
                break
            except FileExistsError:
                suffix += 1
                target = day / "{}_{}".format(name, suffix)
        records = []
        for record in payload["records"]:
            record = dict(record)
            image = annotated.get(record["image"])
            if image is not None:
                jpg_name = Path(record["image"]).with_suffix(".jpg").name
                if cv2.imwrite(str(target / jpg_name), image, [cv2.IMWRITE_JPEG_QUALITY, HISTORY_JPEG_QUALITY]):
                    record["image"] = jpg_name
                else:
                    log.warning("match diagnostics history: cannot write %s", target / jpg_name)
                    record["image"] = ""
            records.append(record)
        archived = dict(payload, records=records)
        with (target / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(archived, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _archive_time(day, run):
        """Epoch seconds from the folder names (local time); mtime when they do not parse."""
        try:
            stamp = datetime.strptime(day.name + run.name[:6], "%Y%m%d%H%M%S")
            return stamp.timestamp() + int(run.name[7:10]) / 1000.0
        except ValueError:
            return run.stat().st_mtime

    @staticmethod
    def _tree_size(path):
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def _cleanup_history(self):
        """Drop archives older than ``history_hours``, then oldest-first until under ``history_max_mb``."""
        cutoff = self._clock() - self.history_hours * 3600
        entries = []  # (mtime, size, path)
        for history in self._root.glob("*/history"):
            for day in history.iterdir():
                if not day.is_dir():
                    continue
                for run in day.iterdir():
                    if run.is_dir():
                        try:
                            entries.append((self._archive_time(day, run), self._tree_size(run), run))
                        except OSError:
                            log.exception("cannot stat %s", run)
        entries.sort(key=lambda item: item[0])
        total = sum(item[1] for item in entries)
        limit = self.history_max_mb * 1024 * 1024
        for mtime, size, run in entries:
            if mtime >= cutoff and total <= limit:
                break
            try:
                shutil.rmtree(str(run))
                total -= size
            except OSError:
                log.exception("cannot delete %s", run)
        for history in self._root.glob("*/history"):
            for day in history.iterdir():
                if day.is_dir():
                    try:
                        day.rmdir()  # only succeeds when empty
                    except OSError:
                        pass
