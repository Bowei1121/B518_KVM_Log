"""Persist and load the most recent JetKVM template matching diagnostics."""

import json
import shutil
from pathlib import Path

import cv2


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

    def __init__(self, template_root, device):
        self.device = str(device).upper()
        self.directory = Path(template_root) / "_captures" / "match_diagnostics" / self.device / "latest"
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
        return payload
