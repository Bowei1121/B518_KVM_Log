import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from match_diagnostics import MatchDiagnostics


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
            recorder.record("window", 0.2, 0.8, note="low similarity")
            recorder.finalize(False, "check")
            latest = MatchDiagnostics.load(root, "BT")
            self.assertFalse(latest["ok"])
            self.assertEqual([item["key"] for item in latest["records"]], ["window"])
            self.assertFalse((MatchDiagnostics.latest_directory(root, "BT") / "01_dock_icon.png").exists())


if __name__ == "__main__":
    unittest.main()
