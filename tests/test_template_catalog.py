import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from template_catalog import TemplateCatalog


class TemplateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "template"
        self.catalog = TemplateCatalog(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_devices_and_all_keys_have_canonical_paths(self):
        for device in ("DFU", "FCT", "BT"):
            for key in self.catalog.keys(device):
                self.assertEqual(
                    self.catalog.path(device, key),
                    self.root / device / "{}_{}.png".format(device, key),
                )
        self.assertIn("dock_icon", self.catalog.keys("DFU"))
        self.assertIn("start_slot4", self.catalog.keys("BT"))
        self.assertNotIn("start_slot1", self.catalog.keys("FCT"))

    def test_invalid_device_key_and_traversal_are_rejected(self):
        for device, key in (("UNKNOWN", "window"), ("FCT", "not_a_role"), ("../FCT", "window"),
                            ("FCT", "../window")):
            with self.assertRaises(ValueError):
                self.catalog.path(device, key)

    def test_first_save_creates_device_folder(self):
        image = np.full((20, 20, 3), 17, dtype=np.uint8)
        target = self.catalog.save_crop(image, (2, 2, 18, 18), "FCT", "window")
        self.assertTrue(target.exists())
        self.assertEqual(target.parent, self.root / "FCT")

    def test_overwrite_requires_confirmation(self):
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        target = self.catalog.save_crop(image, (2, 2, 18, 18), "DFU", "input")
        before = target.read_bytes()
        image[:, :] = 255
        with self.assertRaises(FileExistsError):
            self.catalog.save_crop(image, (2, 2, 18, 18), "DFU", "input")
        self.assertEqual(target.read_bytes(), before)
        self.catalog.save_crop(image, (2, 2, 18, 18), "DFU", "input", overwrite=True)
        self.assertNotEqual(target.read_bytes(), before)

    def test_invalid_crop_is_rejected(self):
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            self.catalog.save_crop(image, (0, 0, 4, 4), "BT", "window")
        with self.assertRaises(ValueError):
            self.catalog.save_crop(image, (0, 0, 21, 5), "BT", "window")


if __name__ == "__main__":
    unittest.main()
