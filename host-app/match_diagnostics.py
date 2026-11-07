"""Persist and load the most recent JetKVM template matching diagnostics."""

import errno
import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2


log = logging.getLogger(__name__)

# History archive (JPEG only). Core has no shared settings mechanism for these, so they are module
# constants, overridable per instance through the constructor.
HISTORY_HOURS = 24
HISTORY_MAX_MB = 2048
HISTORY_JPEG_QUALITY = 85
# A staging folder untouched this long belongs to an interrupted archive. Younger ones may be another
# process (e.g. jetkvm_auto focus) still writing into the same template root, so they are never swept.
STAGING_STALE_SEC = 3600
PUBLISH_ATTEMPTS = 10


# Archive and cleanup run on one daemon worker thread: the Core TCP event loop never waits for JPEG encoding or
# directory scans, a single worker serializes every write and sweep under the shared archive root, and a daemon
# thread never delays Core's exit (an unfinished archive is simply lost at shutdown).
# The queue is bounded in jobs and in raw pixel bytes; when full, the new archive is dropped with a log line
# (``latest/`` is unaffected), so a stuck disk cannot pile up memory.
HISTORY_MAX_PENDING_JOBS = 8
HISTORY_MAX_PENDING_MB = 128

_jobs = queue.Queue()
_pending_lock = threading.Lock()
_pending = {"jobs": 0, "bytes": 0}
_worker = {"thread": None}


def _worker_loop():
    while True:
        job, nbytes = _jobs.get()
        try:
            job()
        except Exception:
            log.exception("match diagnostics history job failed")
        finally:
            job = None  # an idle worker must not keep the last raw frames alive
            with _pending_lock:
                _pending["jobs"] -= 1
                _pending["bytes"] -= nbytes


def _submit(job, nbytes):
    """Queue ``job`` for the worker. Returns False (and logs) when the queue is full or cannot start."""
    with _pending_lock:
        if _pending["jobs"] >= HISTORY_MAX_PENDING_JOBS or \
                _pending["bytes"] + nbytes > HISTORY_MAX_PENDING_MB * 1024 * 1024:
            log.warning("match diagnostics history queue full (%d jobs, %d bytes); archive skipped",
                        _pending["jobs"], _pending["bytes"])
            return False
        if _worker["thread"] is None or not _worker["thread"].is_alive():
            thread = threading.Thread(target=_worker_loop, name="match-diag-history", daemon=True)
            thread.start()
            _worker["thread"] = thread
        _pending["jobs"] += 1
        _pending["bytes"] += nbytes
    _jobs.put((job, nbytes))
    return True


def wait_for_history(timeout=30.0):
    """Block until every queued history job finished (tests). Returns False on timeout."""
    done = threading.Event()
    with _pending_lock:
        _pending["jobs"] += 1  # the sentinel bypasses the cap
        if _worker["thread"] is None or not _worker["thread"].is_alive():
            thread = threading.Thread(target=_worker_loop, name="match-diag-history", daemon=True)
            thread.start()
            _worker["thread"] = thread
    _jobs.put((done.set, 0))
    return done.wait(timeout)


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


def _text(image, text, origin, color, scale=0.5):
    """Readable text on any background: a black outline under the colour."""
    # Same thickness for every pass: the glyph advance depends on it, so a thicker outline would drift off the text.
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        cv2.putText(image, text, (origin[0] + dx, origin[1] + dy), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1)


REASON_ABBREVIATIONS = {"no_hole": "NH", "hole_size": "HS", "hole_offset": "HO", "scale_range": "SR",
                        "not_square": "NS"}
LEFT_COLOR, RIGHT_COLOR, REJECT_COLOR, PAIR_COLOR = (255, 200, 0), (255, 0, 255), (150, 150, 150), (0, 220, 0)
ZOOM_FACTOR = 4
ZOOM_MAX_SOURCE_WIDTH = 480


def _draw_locating(image, explanation, tf, size=1):
    """Locator candidates (left/right colours), near-miss boxes with reason, the chosen pair and the gate samples.

    ``tf`` maps frame coordinates to ``image`` pixels; ``size`` scales line widths and text for zoomed pictures.
    """
    locate = explanation.get("locate")
    if not locate:
        return

    def point(x, y):
        px, py = tf(x, y)
        return int(round(px)), int(round(py))

    def rect(box, color, thickness):
        x, y, w, h = box
        cv2.rectangle(image, point(x, y), point(x + w, y + h), color, thickness)

    for item in locate["rejected"]:
        rect(item["box"], REJECT_COLOR, size)
        px, py = point(item["box"][0], item["box"][1])
        _text(image, REASON_ABBREVIATIONS.get(item["reason"], item["reason"]), (px, max(10, py - 3)),
              REJECT_COLOR, 0.35 if size == 1 else 0.6)
    for key, color in (("left", LEFT_COLOR), ("right", RIGHT_COLOR)):
        for item in locate[key]:
            rect(item["box"], color, size)
    selected = locate.get("selected")
    if selected is not None:
        left, right = locate["left"][selected["left"]], locate["right"][selected["right"]]
        cv2.line(image, point(*left["hole_centre"]), point(*right["hole_centre"]), PAIR_COLOR, 2 * size)
        origin = point(*selected["origin"])
        cv2.drawMarker(image, origin, PAIR_COLOR, cv2.MARKER_CROSS, 14 * size, 2)
        _text(image, "scale {:.3f}".format(selected["scale"]), (origin[0] + 6, origin[1] + 14 * size),
              PAIR_COLOR, 0.45 if size == 1 else 0.7)
    samples = explanation.get("samples")
    if samples:
        for key, color in (("gutter", (0, 165, 255)), ("marker", (0, 0, 255)), ("heartbeat", (255, 255, 0))):
            for x, y in samples[key]:
                cv2.circle(image, point(x, y), 2 * size, color, -1 if size == 1 else 1)


def draw_locator_zoom(frame, explanation):
    """4x crop around the top of the Log Solution window (or the densest candidates) with the locating overlay.

    None when there is nothing to show. Never raises.
    """
    try:
        locate = explanation.get("locate") or {}
        boxes = [c["box"] for c in locate.get("left", []) + locate.get("right", [])] + \
            [r["box"] for r in locate.get("rejected", [])]
        if explanation.get("located"):
            x0, y0 = explanation["origin"]
            scale = explanation["scale"]
            left, top = x0 - 20, y0 - 15
            right, bottom = x0 + 376 * scale + 20, y0 + 40 * scale + 15
        elif boxes:
            # No pair: centre on the box with the most neighbours within one window width.
            centres = [(b[0] + b[2] / 2.0, b[1] + b[3] / 2.0) for b in boxes]
            best = max(centres, key=lambda c: sum(1 for o in centres if abs(o[0] - c[0]) <= ZOOM_MAX_SOURCE_WIDTH / 2
                                                  and abs(o[1] - c[1]) <= 60))
            left, top = best[0] - ZOOM_MAX_SOURCE_WIDTH / 2, best[1] - 60
            right, bottom = best[0] + ZOOM_MAX_SOURCE_WIDTH / 2, best[1] + 60
        else:
            return None
        height, width = frame.shape[:2]
        left, top = max(0, int(left)), max(0, int(top))
        right, bottom = min(width, int(right)), min(height, int(bottom))
        right = min(right, left + ZOOM_MAX_SOURCE_WIDTH + 80)
        if right - left < 8 or bottom - top < 8:
            return None
        crop = cv2.resize(frame[top:bottom, left:right], None, fx=ZOOM_FACTOR, fy=ZOOM_FACTOR,
                          interpolation=cv2.INTER_NEAREST)
        _draw_locating(crop, explanation, lambda x, y: ((x - left) * ZOOM_FACTOR, (y - top) * ZOOM_FACTOR),
                       ZOOM_FACTOR)
        summary = locate.get("summary")
        if summary:
            _text(crop, summary, (8, 20), (255, 255, 255), 0.55)
        return crop
    except Exception:
        log.exception("match diagnostics: cannot draw the locator zoom")
        return None


def draw_reading_explanation(image, explanation, first_line_y=56):
    """Draw an ``explain_app_frame`` result onto ``image`` (in place): origin, locators, result band and cells,
    the scale / state / reason, the status of each cell, and the sampled colours of a failing cell.

    Only the plain data of the explanation is used. Never raises: a drawing error is logged and skipped.
    """
    try:
        _draw_reading_explanation(image, explanation, first_line_y)
    except Exception:
        log.exception("match diagnostics: cannot draw the reading explanation")


def _draw_reading_explanation(image, explanation, first_line_y):
    def rect(box, color, thickness=1):
        x, y, w, h = box
        cv2.rectangle(image, (int(round(x)), int(round(y))), (int(round(x + w)), int(round(y + h))), color, thickness)

    green, red, yellow, white = (0, 220, 0), (0, 0, 255), (0, 255, 255), (255, 255, 255)
    _draw_locating(image, explanation, lambda x, y: (x, y))
    lines = []
    if not explanation.get("located"):
        lines.append(("Log Solution not located ({})".format(explanation.get("reason") or "?"), red))
        if explanation.get("locate"):
            lines.append((explanation["locate"]["summary"], white))
    else:
        origin = explanation["origin"]
        for box in explanation["locators"]:
            rect(box, yellow, 2)
        if explanation.get("band"):
            rect(explanation["band"], (255, 128, 0), 1)
        cv2.drawMarker(image, (int(round(origin[0])), int(round(origin[1]))), yellow, cv2.MARKER_CROSS, 14, 2)
        lines.append(("origin=({:.0f},{:.0f}) scale={:.3f} state={} reliable={} reason={}".format(
            origin[0], origin[1], explanation["scale"], explanation.get("state"),
            explanation.get("reliable"), explanation.get("reason") or "-"), white))
        statuses = []
        for cell in explanation["cells"]:
            ok = cell["status"] is not None
            rect(cell["box"], green if ok else red, 1 if ok else 2)
            x, y = cell["box"][0], cell["box"][1]
            _text(image, str(cell["slot"]), (int(x) + 2, int(y) + 12), green if ok else red, 0.4)
            statuses.append("{}:{}".format(cell["slot"], cell["status"] or "?"))
        if statuses:
            lines.append(("cells " + " ".join(statuses), white))
        failure = explanation.get("failure")
        if failure:
            lines.append(("cell {} corner samples (BGR):".format(failure["slot"]), red))
            for sample in failure["samples"]:
                px, py = sample["point"]
                cv2.circle(image, (int(round(px)), int(round(py))), 3, red, -1)
                b, g, r = sample["bgr"]
                lines.append(("  ({:.0f},{:.0f}) BGR=({},{},{}) nearest={} dist={}".format(
                    px, py, b, g, r, sample["nearest"], sample["distance"]), red))
    for index, (text, color) in enumerate(lines):
        _text(image, text, (12, first_line_y + 20 * index), color)


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

    def record(self, key, score, threshold, frame=None, box=None, note=None, color=(0, 200, 255),
               explanation=None, matched=None, plain=False):
        if matched is None:
            matched = score is not None and box is not None and score >= threshold
        image_name = ""
        if frame is not None:
            annotated = frame.copy()
            if plain:  # a prepared picture (e.g. the locator zoom): keep it as is
                pass
            elif box is not None:
                x, y, width, height = box
                cv2.rectangle(annotated, (x, y), (x + width, y + height), color, 2)
            if not plain:
                score_text = "n/a" if score is None else "{:.3f}".format(score)
                label = "{} {} ({})".format(key, score_text, "MATCH" if matched else "MISS")
                cv2.putText(annotated, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
            if explanation is not None:
                draw_reading_explanation(annotated, explanation)
            index = len(self.records) + 1
            image_name = "{:02d}_{}.png".format(index, key)
            self._annotated[index] = (image_name[:-4], annotated)
            if not cv2.imwrite(str(self.directory / image_name), annotated):
                log.warning("match diagnostics: cannot write %s", self.directory / image_name)
                image_name = ""
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
        try:  # preparing and queuing the archive is diagnostics only: never fail the flow
            annotated, self._annotated = self._annotated, {}
            snapshot = dict(payload, records=[dict(record) for record in payload["records"]])
            stamp = datetime.fromtimestamp(self._clock())
            nbytes = sum(image.nbytes for _stem, image in annotated.values())
            _submit(lambda: self._archive_and_cleanup(snapshot, annotated, ok, operation, stamp), nbytes)
        except Exception:
            log.exception("match diagnostics history archive could not be queued")
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
        history = self._root / self.device / "history"
        day = history / stamp.strftime("%Y%m%d")
        # Build the archive in a staging folder outside history/ and publish it with one rename, so an exit
        # or crash mid-write never leaves a half archive that looks complete. Leftovers are swept on cleanup.
        staging = self._root / self.device / "history_tmp" / uuid.uuid4().hex
        staging.mkdir(parents=True)
        records = []
        for index, record in enumerate(payload["records"], 1):
            record = dict(record)
            item = annotated.get(index)
            if item is not None:
                stem, image = item
                jpg_name = stem + ".jpg"
                if cv2.imwrite(str(staging / jpg_name), image, [cv2.IMWRITE_JPEG_QUALITY, HISTORY_JPEG_QUALITY]):
                    record["image"] = jpg_name
                else:
                    log.warning("match diagnostics history: cannot write %s", staging / jpg_name)
                    record["image"] = ""
            records.append(record)
        archived = dict(payload, records=records)
        with (staging / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(archived, handle, ensure_ascii=False, indent=2)
        # Same millisecond/operation/result: continue after the highest existing suffix, so the newest copy
        # never reuses a name that cleanup freed (and then sorts as oldest).
        for attempt in range(PUBLISH_ATTEMPTS):
            # Without a cross-process lock, another Core/tool process may remove an empty day folder (cleanup)
            # or publish the same name first. The whole mkdir/list/rename step is retried: a vanished day
            # folder is recreated and an occupied target picks the next suffix.
            try:
                day.mkdir(parents=True, exist_ok=True)
                highest = 0
                for existing in day.iterdir():
                    if existing.name == name:
                        highest = max(highest, 1)
                    elif existing.name.startswith(name + "_") and existing.name[len(name) + 1:].isdigit():
                        highest = max(highest, int(existing.name[len(name) + 1:]))
                target = day / (name if highest == 0 else "{}_{}".format(name, highest + 1))
                os.rename(str(staging), str(target))
                break
            except OSError as exc:
                retryable = isinstance(exc, (FileNotFoundError, FileExistsError)) or \
                    exc.errno in (errno.EEXIST, errno.ENOTEMPTY)
                if not retryable or attempt == PUBLISH_ATTEMPTS - 1 or not staging.exists():
                    raise
        try:
            staging.parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _archive_time(day, run):
        """Epoch seconds from the folder names (local time); mtime when they do not parse."""
        try:
            stamp = datetime.strptime(day.name + run.name[:6], "%Y%m%d%H%M%S")
            return stamp.timestamp() + int(run.name[7:10]) / 1000.0
        except ValueError:
            return run.stat().st_mtime

    def _sweep_stale_staging(self):
        """Delete staging folders of interrupted archives; fresh ones may belong to a live writer (any process)."""
        limit = time.time() - STAGING_STALE_SEC
        for staging in self._root.glob("*/history_tmp/*"):
            try:
                if staging.stat().st_mtime >= limit:
                    continue
                shutil.rmtree(str(staging))
            except FileNotFoundError:
                continue
            except OSError:
                log.exception("cannot delete stale staging folder %s", staging)
        for parent in self._root.glob("*/history_tmp"):
            try:
                parent.rmdir()  # only when empty
            except OSError:
                pass

    @staticmethod
    def _collision_index(name):
        """1 for the first archive of a millisecond, N for its ``_N`` collision copies."""
        tail = name.rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 1

    @staticmethod
    def _tree_size(path):
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def _cleanup_history(self):
        """Drop archives older than ``history_hours``, then oldest-first until under ``history_max_mb``."""
        cutoff = self._clock() - self.history_hours * 3600
        self._sweep_stale_staging()
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
        entries.sort(key=lambda item: (item[0], self._collision_index(item[2].name)))
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
