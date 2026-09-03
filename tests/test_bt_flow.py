import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from auto_flow import run_check, run_flow
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog


class FakeKvm:
    def __init__(self, frame):
        self.frame = frame
        self.rpc_errors = []
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


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


if __name__ == "__main__":
    unittest.main()
