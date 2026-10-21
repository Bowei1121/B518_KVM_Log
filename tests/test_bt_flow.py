import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from hid_session import run_focus
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog
from fake_kvm import FakeKVM


class FakeKvm(FakeKVM):
    def __init__(self, frame):
        super().__init__(frame)
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FailingKvm(FakeKvm):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


class BtFlowTests(unittest.TestCase):
    def test_fct_focus_clicks_dock_and_persists_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = TemplateCatalog(Path(temp) / "template")
            rng = np.random.RandomState(36)
            frame = rng.randint(0, 255, (100, 160, 3), dtype=np.uint8)
            catalog.save_crop(frame, (105, 15, 145, 55), "FCT", "dock_icon")
            kvm = FakeKvm(frame)

            result = asyncio.run(run_focus(kvm, "FCT", template_root=catalog.root, threshold=0.8))

            self.assertTrue(result["ok"])
            self.assertEqual(len(kvm.clicks), 1)
            self.assertGreater(kvm.clicks[0][0], 100)
            summary = MatchDiagnostics.load(catalog.root, "FCT")
            self.assertEqual(summary["operation"], "focus")
            self.assertEqual(summary["records"][0]["key"], "dock_icon")

    def test_fct_focus_fails_closed_when_template_missing_or_hid_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            missing_kvm = FakeKvm(np.zeros((30, 30, 3), dtype=np.uint8))
            missing = asyncio.run(run_focus(missing_kvm, "FCT", template_root=root, log=lambda *_: None))
            self.assertFalse(missing["ok"])
            self.assertEqual(missing_kvm.clicks, [])

            catalog = TemplateCatalog(root)
            frame = np.random.RandomState(44).randint(0, 255, (80, 120, 3), dtype=np.uint8)
            catalog.save_crop(frame, (60, 10, 100, 50), "FCT", "dock_icon")
            failing_kvm = FailingKvm(frame)
            failed = asyncio.run(run_focus(failing_kvm, "FCT", template_root=root, threshold=0.8))
            self.assertFalse(failed["ok"])
            self.assertIn("hid_error", failed)


if __name__ == "__main__":
    unittest.main()
