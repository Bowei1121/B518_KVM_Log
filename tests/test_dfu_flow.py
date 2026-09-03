import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import dfu_flow
from template_catalog import TemplateCatalog


class FakeKvm:
    def __init__(self, frame):
        self.frame = frame
        self.rpc_errors = []
        self.events = []

    async def click(self, x, y):
        self.events.append(("click", x, y))

    async def type_text(self, text):
        self.events.append(("type", text))

    async def press_enter(self):
        self.events.append(("enter",))

    async def press_command_shift_m(self):
        self.events.append(("log_shortcut",))


class DfuFlowTests(unittest.TestCase):
    def test_parser_sorts_sparse_slots_and_rejects_invalid_payloads(self):
        self.assertEqual(dfu_flow.parse_sn_payload("3:SN3,1:SN1", 4), [(1, "SN1"), (3, "SN3")])
        for payload in ("", "1:", "a:SN", "1:SN,1:OTHER", "5:SN", "1:SN:EXTRA"):
            with self.assertRaises(ValueError, msg=payload):
                dfu_flow.parse_sn_payload(payload, 4)

    def test_profile_requires_explicit_valid_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            with self.assertRaises(ValueError):
                dfu_flow.load_profile(root, "1")
            root.mkdir(parents=True)
            (root / "device_profiles.json").write_text('{"DFU":{"1":"bad"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                dfu_flow.load_profile(root, "1")
            (root / "device_profiles.json").write_text('{"DFU":{"1":"4slot"}}', encoding="utf-8")
            self.assertEqual(dfu_flow.load_profile(root, "1"), ("4slot", 4))

    def _prepare_catalog(self, root, profile="4slot"):
        catalog = TemplateCatalog(root)
        image = np.random.RandomState(4).randint(0, 255, (40, 40, 3), dtype=np.uint8)
        keys = ["dock_icon", "window", "checkbox_checked", "checkbox_unchecked", "input", "button"]
        keys += ["slot{}_{}".format(i, profile) for i in range(1, 5)]
        for key in keys:
            catalog.save_crop(image, (2, 2, 30, 30), "DFU", key)
        (catalog.root / "device_profiles.json").write_text(
            json.dumps({"DFU": {"1": profile}}), encoding="utf-8"
        )
        return catalog

    def test_input_orders_dock_checkbox_sn_ok_and_log_shortcut(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = self._prepare_catalog(Path(temp) / "template")
            frame = np.zeros((80, 80, 3), dtype=np.uint8)
            kvm = FakeKvm(frame)
            rows = {slot: (1, slot * 10, 60, 8) for slot in range(1, 5)}
            calls = {"verify": 0}

            async def focused(kvm, *args, **kwargs):
                await kvm.click(70, 70)

            def state(_frame, _row, _checked, _unchecked, _diag, slot, _threshold):
                # Initial screen needs slot 1 toggled; verification is then fully correct.
                desired = "checked" if slot in (1, 3) else "unchecked"
                current = "unchecked" if slot == 1 and calls["verify"] == 1 else desired
                return current, (10, slot * 10, 5, 5), .95, .10

            def window(*_args):
                calls["verify"] += 1
                return .99, (0, 0, 70, 70)

            with patch.object(dfu_flow, "_focus_dock", focused), \
                 patch.object(dfu_flow, "_window_match", window), \
                 patch.object(dfu_flow, "_slot_rows", lambda *_args: rows), \
                 patch.object(dfu_flow, "_best_state", state), \
                 patch.object(dfu_flow, "_locate_control", lambda *_args: (30, 30, 10, 10)):
                result = asyncio.run(dfu_flow.run_dfu_input(kvm, "1", "3:SN3,1:SN1", catalog.root))

            self.assertTrue(result["ok"])
            self.assertEqual(kvm.events[0], ("click", 70, 70))
            self.assertIn(("type", "SN1"), kvm.events)
            self.assertIn(("type", "SN3"), kvm.events)
            self.assertLess(kvm.events.index(("type", "SN1")), kvm.events.index(("type", "SN3")))
            self.assertEqual(kvm.events.count(("enter",)), 2)
            self.assertEqual(kvm.events[-1], ("log_shortcut",))
            # Dock, checkbox correction, input, one OK; no extra checkbox retry.
            self.assertEqual(len([event for event in kvm.events if event[0] == "click"]), 4)

    def test_ambiguous_checkbox_stops_before_typing(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = self._prepare_catalog(Path(temp) / "template")
            kvm = FakeKvm(np.zeros((80, 80, 3), dtype=np.uint8))
            with patch.object(dfu_flow, "_focus_dock", lambda *_args, **_kwargs: asyncio.sleep(0)), \
                 patch.object(dfu_flow, "_window_match", lambda *_args: (.99, (0, 0, 70, 70))), \
                 patch.object(dfu_flow, "_slot_rows", lambda *_args: {1: (0, 0, 20, 10), 2: (0, 10, 20, 10), 3: (0, 20, 20, 10), 4: (0, 30, 20, 10)}), \
                 patch.object(dfu_flow, "_best_state", side_effect=ValueError("slot 1 checkbox 狀態不明確")):
                result = asyncio.run(dfu_flow.run_dfu_input(kvm, "1", "1:SN1", catalog.root))
            self.assertFalse(result["ok"])
            self.assertFalse(any(event[0] == "type" for event in kvm.events))

    def test_check_returns_all_slots_and_notest_without_sn(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = self._prepare_catalog(Path(temp) / "template")
            # Add Log templates required by the check state machine.
            image = np.random.RandomState(7).randint(0, 255, (40, 40, 3), dtype=np.uint8)
            for key in ("log_dock_icon", "log_window", "log_testing", "log_pass", "log_fail", "log_notest") + tuple("log_slot{}_4slot".format(i) for i in range(1, 5)):
                catalog.save_crop(image, (2, 2, 30, 30), "DFU", key)
            kvm = FakeKvm(np.zeros((80, 80, 3), dtype=np.uint8))
            values = iter([("pass", "SN1"), ("notest", ""), ("fail", "SN3"), ("pass", "SN4")])
            with patch.object(dfu_flow, "_focus_dock", lambda *_args, **_kwargs: asyncio.sleep(0)), \
                 patch.object(dfu_flow, "_window_match", lambda *_args: (.99, (0, 0, 70, 70))), \
                 patch.object(dfu_flow, "_slot_rows", lambda *_args: {i: (0, i * 10, 60, 8) for i in range(1, 5)}), \
                 patch.object(dfu_flow, "_classify_log_row", lambda *_args: next(values)):
                result = asyncio.run(dfu_flow.run_dfu_check(kvm, "1", catalog.root))
            self.assertTrue(result["ok"])
            self.assertEqual(result["rows"], [(1, "SN1", "pass"), (2, "", "notest"), (3, "SN3", "fail"), (4, "SN4", "pass")])


if __name__ == "__main__":
    unittest.main()
