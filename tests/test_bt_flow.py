import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from auto_flow import run_check, run_flow, run_focus
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog


class FakeKvm:
    def __init__(self, frame):
        self.frame = frame
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FailingKvm(FakeKvm):
    async def click(self, x, y):
        await super().click(x, y)
        self.rpc_errors.append("hidg endpoint disconnected")


class BtFlowTests(unittest.TestCase):
    def test_start_all_clicks_dock_before_start_all(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = TemplateCatalog(Path(temp) / "template")
            rng = np.random.RandomState(12)
            frame = rng.randint(0, 255, (180, 260, 3), dtype=np.uint8)
            catalog.save_crop(frame, (15, 20, 125, 140), "BT", "window")
            catalog.save_crop(frame, (185, 15, 225, 55), "BT", "dock_icon")
            catalog.save_crop(frame, (50, 75, 90, 110), "BT", "start_all")
            kvm = FakeKvm(frame)

            result = asyncio.run(run_flow(kvm, "BT", template_root=catalog.root, mode="button", threshold=0.8))

            self.assertTrue(result["ok"])
            self.assertEqual(len(kvm.clicks), 2)
            self.assertGreater(kvm.clicks[0][0], 180)  # Dock is at the right side of the full frame.
            self.assertLess(kvm.clicks[1][0], 125)     # Start All is inside the window ROI.

    def test_bt_input_is_rejected_without_hid(self):
        with tempfile.TemporaryDirectory() as temp:
            kvm = FakeKvm(np.zeros((30, 30, 3), dtype=np.uint8))
            result = asyncio.run(run_flow(kvm, "BT", template_root=Path(temp), mode="input", log=lambda _msg: None))
            self.assertFalse(result["ok"])
            self.assertEqual(kvm.clicks, [])

    def test_check_does_not_send_hid_and_persists_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = TemplateCatalog(Path(temp) / "template")
            rng = np.random.RandomState(24)
            frame = rng.randint(0, 255, (180, 260, 3), dtype=np.uint8)
            catalog.save_crop(frame, (15, 20, 125, 140), "BT", "window")
            catalog.save_crop(frame, (40, 50, 75, 80), "BT", "testing")
            kvm = FakeKvm(frame)

            result = asyncio.run(run_check(kvm, "BT", template_root=catalog.root, threshold=0.8))

            self.assertTrue(result["ok"])
            self.assertTrue(result["testing"])
            self.assertEqual(kvm.clicks, [])
            summary = MatchDiagnostics.load(catalog.root, "BT")
            self.assertEqual([record["key"] for record in summary["records"]], ["window", "testing"])

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

    def test_fct_input_and_button_are_rejected_without_hid(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode in ("input", "button"):
                kvm = FakeKvm(np.zeros((30, 30, 3), dtype=np.uint8))
                result = asyncio.run(run_flow(
                    kvm, "FCT", template_root=Path(temp), mode=mode, log=lambda _msg: None
                ))
                self.assertFalse(result["ok"])
                self.assertEqual(kvm.clicks, [])

    def test_fct_focus_fails_closed_when_template_missing_or_hid_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            missing_kvm = FakeKvm(np.zeros((30, 30, 3), dtype=np.uint8))
            missing = asyncio.run(run_focus(missing_kvm, "FCT", template_root=root, log=lambda _msg: None))
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
