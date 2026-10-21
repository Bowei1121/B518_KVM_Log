import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from match_diagnostics import MatchDiagnostics, note_for_language


class MatchDiagnosticsTests(unittest.TestCase):
    def test_persists_only_latest_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            recorder.record("dock_icon", 0.9, 0.8, np.zeros((40, 40, 3), dtype=np.uint8), (1, 1, 10, 10))
            recorder.finalize(True, "button")
            first = MatchDiagnostics.load(root, "BT")
            self.assertTrue(first["ok"])
            self.assertEqual(first["records"][0]["key"], "dock_icon")
            self.assertTrue((MatchDiagnostics.latest_directory(root, "BT") / "01_dock_icon.png").exists())

            recorder = MatchDiagnostics(root, "BT")
            recorder.record("window", 0.2, 0.8, note={"en": "low similarity", "zh": "相似度過低"})
            recorder.finalize(False, "check")
            latest = MatchDiagnostics.load(root, "BT")
            self.assertFalse(latest["ok"])
            self.assertEqual([item["key"] for item in latest["records"]], ["window"])
            self.assertFalse((MatchDiagnostics.latest_directory(root, "BT") / "01_dock_icon.png").exists())

    def test_summary_stores_bilingual_note(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            recorder.record("window", 0.2, 0.8, note={"en": "low similarity", "zh": "相似度過低"})
            recorder.record("frame", None, 0.8)
            recorder.record("legacy", 0.1, 0.8, note="plain text")
            recorder.finalize(False, "check")
            notes = [item["note"] for item in MatchDiagnostics.load(root, "BT")["records"]]
            self.assertEqual(notes, [
                {"en": "low similarity", "zh": "相似度過低"},
                {"en": "", "zh": ""},
                {"en": "plain text", "zh": "plain text"},
            ])


class NoteForLanguageTests(unittest.TestCase):
    def test_dict_returns_requested_language(self):
        note = {"en": "No JetKVM frame", "zh": "沒有 JetKVM 影格"}
        self.assertEqual(note_for_language(note, "en"), "No JetKVM frame")
        self.assertEqual(note_for_language(note, "zh"), "沒有 JetKVM 影格")

    def test_dict_missing_language_falls_back_to_english(self):
        self.assertEqual(note_for_language({"en": "expected 4"}, "zh"), "expected 4")
        self.assertEqual(note_for_language({"en": "expected 4", "zh": ""}, "zh"), "expected 4")

    def test_legacy_plain_string_is_returned_as_is(self):
        self.assertEqual(note_for_language("低相似度", "zh"), "低相似度")
        self.assertEqual(note_for_language("low similarity", "en"), "low similarity")

    def test_none_is_empty(self):
        self.assertEqual(note_for_language(None, "zh"), "")


if __name__ == "__main__":
    unittest.main()
