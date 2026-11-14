import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
import match_diagnostics
from match_diagnostics import MatchDiagnostics, note_for_language, wait_for_history


class MatchDiagnosticsTests(unittest.TestCase):
    def test_persists_only_latest_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            recorder.record("dock_icon", 0.9, 0.8, np.zeros((40, 40, 3), dtype=np.uint8), (1, 1, 10, 10))
            recorder.finalize(True, "button")
            wait_for_history()
            first = MatchDiagnostics.load(root, "BT")
            self.assertTrue(first["ok"])
            self.assertEqual(first["records"][0]["key"], "dock_icon")
            self.assertTrue((MatchDiagnostics.latest_directory(root, "BT") / "01_dock_icon.png").exists())

            recorder = MatchDiagnostics(root, "BT")
            recorder.record("window", 0.2, 0.8, note={"en": "low similarity", "zh": "相似度過低"})
            recorder.finalize(False, "check")
            wait_for_history()
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
            wait_for_history()
            notes = [item["note"] for item in MatchDiagnostics.load(root, "BT")["records"]]
            self.assertEqual(notes, [
                {"en": "low similarity", "zh": "相似度過低"},
                {"en": "", "zh": ""},
                {"en": "plain text", "zh": "plain text"},
            ])


def _run(root, op, ok, now, **kwargs):
    recorder = MatchDiagnostics(root, "BT", now=now, **kwargs)
    recorder.record("step", 0.9, 0.8, np.full((60, 80, 3), 128, dtype=np.uint8), (1, 1, 10, 10))
    recorder.finalize(ok, op)
    wait_for_history()
    return recorder


def _history(root):
    base = Path(root) / "_captures" / "match_diagnostics" / "BT" / "history"
    return sorted(p for p in base.glob("*/*") if p.is_dir()) if base.exists() else []


class HistoryTests(unittest.TestCase):
    def test_two_runs_archived_as_jpeg_and_latest_keeps_second(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = time.time()
            _run(root, "monitor_start", False, now)
            _run(root, "check", True, now + 1)
            runs = _history(root)
            self.assertEqual(len(runs), 2)
            self.assertTrue(runs[0].name.endswith("_monitor_start_FAIL"))
            self.assertTrue(runs[1].name.endswith("_check_OK"))
            for run in runs:
                names = sorted(p.name for p in run.iterdir())
                self.assertEqual(names, ["01_step.jpg", "summary.json"])
            import json
            summary = json.loads((runs[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["records"][0]["image"], "01_step.jpg")
            self.assertIsNotNone(cv2.imread(str(runs[0] / "01_step.jpg")))
            latest = MatchDiagnostics.load(root, "BT")
            self.assertEqual(latest["operation"], "check")
            self.assertEqual(latest["records"][0]["image"], "01_step.png")
            latest_dir = MatchDiagnostics.latest_directory(root, "BT")
            self.assertEqual(sorted(p.name for p in latest_dir.iterdir()), ["01_step.png", "summary.json"])

    def test_old_archives_are_deleted_and_empty_day_folder_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = time.time()
            _run(root, "old", True, now - 25 * 3600)
            _run(root, "recent", True, now - 3600)
            _run(root, "new", True, now)
            self.assertEqual([p.name[11:] for p in _history(root)], ["recent_OK", "new_OK"])
            for day in (root / "_captures" / "match_diagnostics" / "BT" / "history").iterdir():
                self.assertTrue(any(day.iterdir()))

    def test_over_capacity_deletes_oldest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = time.time()
            _run(root, "a", True, now - 30)
            one = sum(f.stat().st_size for f in _history(root)[0].iterdir())
            limit_mb = (one * 2.5) / (1024 * 1024)
            _run(root, "b", True, now - 20, history_max_mb=limit_mb)
            _run(root, "c", True, now - 10, history_max_mb=limit_mb)
            self.assertEqual([p.name[11:] for p in _history(root)], ["b_OK", "c_OK"])

    def test_archive_write_failure_does_not_fail_the_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            real = cv2.imwrite
            recorder.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))

            def boom(path, *args):
                if str(path).endswith(".jpg"):
                    raise OSError("disk full")
                return real(path, *args)

            with mock.patch("match_diagnostics.cv2.imwrite", side_effect=boom):
                payload = recorder.finalize(False, "check")
                wait_for_history()
            self.assertFalse(payload["ok"])
            self.assertTrue(MatchDiagnostics.load(root, "BT")["records"])

    def test_cleanup_failure_does_not_fail_the_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            with mock.patch.object(MatchDiagnostics, "_cleanup_history", side_effect=OSError("x")):
                payload = _run(root, "check", True, time.time())
            wait_for_history()
            self.assertEqual(len(_history(root)), 1)
            self.assertIsNotNone(payload)

    def test_same_millisecond_runs_keep_both_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = 1_700_000_000.0
            first = MatchDiagnostics(root, "BT", now=now)
            first.record("one", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            first.finalize(False, "monitor_start")
            second = MatchDiagnostics(root, "BT", now=now)
            second.record("two", 0.1, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            second.finalize(False, "monitor_start")
            wait_for_history()
            import json
            runs = _history(root)
            self.assertEqual(len(runs), 2)
            keys = sorted(json.loads((r / "summary.json").read_text(encoding="utf-8"))["records"][0]["key"]
                          for r in runs)
            self.assertEqual(keys, ["one", "two"])

    def test_jpeg_write_returning_false_is_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            real = cv2.imwrite
            recorder.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))

            def refuse(path, *args):
                return False if str(path).endswith(".jpg") else real(path, *args)

            with mock.patch("match_diagnostics.cv2.imwrite", side_effect=refuse):
                with self.assertLogs("match_diagnostics", level="WARNING") as logs:
                    recorder.finalize(True, "check")
                    wait_for_history()
            self.assertIn("01_step.jpg", logs.output[0])
            import json
            summary = json.loads((_history(root)[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["records"][0]["image"], "")

    def test_finalize_does_not_wait_for_slow_history_sweep(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            recorder = MatchDiagnostics(root, "BT")
            recorder.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            with mock.patch.object(MatchDiagnostics, "_cleanup_history", side_effect=lambda: time.sleep(0.4)):
                started = time.monotonic()
                recorder.finalize(True, "check")
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.2)
                wait_for_history()
            self.assertEqual(len(_history(root)), 1)


class ConfirmTimeoutDiagnosticsTests(unittest.TestCase):
    def test_monitor_start_confirm_timeout_records_last_unreliable_frame(self):
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from hid_session import FlowError
        from protocol_helpers import LogSolutionKVM
        from template_catalog import TemplateCatalog
        import monitor_flow

        class Gate:
            stats = None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            seed = LogSolutionKVM()
            frame = seed.latest_frame().image
            x, y, w, h = seed.dock_icon_box()
            TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                            "log_solution_dock_icon")
            kvm = LogSolutionKVM(state="standby")
            kvm.close_window()
            with self.assertRaises(FlowError) as raised:
                asyncio.run(monitor_flow.run_monitor_start(
                    kvm, Gate(), "DFU-01", template_root=root, confirm_sec=0.1, start_timeout_sec=0.1))
            self.assertEqual(raised.exception.code, "log_solution_not_found")
            wait_for_history()
            latest = MatchDiagnostics.load(root, "DFU-01")
            record = [r for r in latest["records"] if r["key"] == "confirm_last_unreliable"][0]
            self.assertTrue(record["image"].endswith(".png"))
            self.assertTrue((MatchDiagnostics.latest_directory(root, "DFU-01") / record["image"]).exists())
            self.assertIn("last reading", record["note"]["en"])
            self.assertIn("最後判讀", record["note"]["zh"])


class QueueBoundTests(unittest.TestCase):
    def test_stuck_worker_caps_pending_jobs_and_never_blocks_finalize(self):
        import threading
        release = threading.Event()
        started = threading.Event()

        def stuck():
            started.set()
            release.wait(10)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            self.assertTrue(match_diagnostics._submit(stuck, 0))
            self.assertTrue(started.wait(5))
            try:
                with self.assertLogs("match_diagnostics", level="WARNING") as logs:
                    t0 = time.monotonic()
                    for _ in range(match_diagnostics.HISTORY_MAX_PENDING_JOBS + 4):
                        rec = MatchDiagnostics(root, "BT")
                        rec.record("big", 0.9, 0.8, np.zeros((1080, 1920, 3), dtype=np.uint8), (1, 1, 5, 5))
                        rec.finalize(True, "check")
                    self.assertLess(time.monotonic() - t0, 5)
                self.assertTrue(any("queue full" in line for line in logs.output))
                self.assertLessEqual(match_diagnostics._pending["jobs"],
                                     match_diagnostics.HISTORY_MAX_PENDING_JOBS)
                self.assertLessEqual(match_diagnostics._pending["bytes"],
                                     match_diagnostics.HISTORY_MAX_PENDING_MB * 1024 * 1024)
                self.assertIsNotNone(MatchDiagnostics.load(root, "BT"))
            finally:
                release.set()
                wait_for_history()

    def test_worker_is_a_daemon_thread_so_core_exit_never_waits(self):
        match_diagnostics._submit(lambda: None, 0)
        wait_for_history()
        self.assertTrue(match_diagnostics._worker["thread"].daemon)

    def test_queue_failure_keeps_return_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            rec = MatchDiagnostics(root, "BT")
            rec.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            with mock.patch.object(match_diagnostics, "_submit", side_effect=RuntimeError("no thread")):
                payload = rec.finalize(True, "check")
            self.assertTrue(payload["ok"])

    def test_queue_failure_keeps_original_flow_error(self):
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from hid_session import FlowError
        from protocol_helpers import LogSolutionKVM
        from template_catalog import TemplateCatalog
        import monitor_flow

        class Gate:
            stats = None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            seed = LogSolutionKVM()
            frame = seed.latest_frame().image
            x, y, w, h = seed.dock_icon_box()
            TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                            "log_solution_dock_icon")
            kvm = LogSolutionKVM(state="standby")
            kvm.close_window()
            with mock.patch.object(match_diagnostics, "_submit", side_effect=RuntimeError("no thread")):
                with self.assertRaises(FlowError) as raised:
                    asyncio.run(monitor_flow.run_monitor_start(
                        kvm, Gate(), "DFU-01", template_root=root, confirm_sec=0.1, start_timeout_sec=0.1,
                        log=lambda *_: None))
            self.assertEqual(raised.exception.code, "log_solution_not_found")

    def test_collision_copies_are_deleted_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = 1_700_000_000.0
            _run(root, "monitor_start", False, now)
            one = sum(f.stat().st_size for f in _history(root)[0].iterdir())
            limit_mb = (one * 2.5) / (1024 * 1024)
            for _ in range(2):
                _run(root, "monitor_start", False, now, history_max_mb=limit_mb)
            self.assertEqual([p.name.rsplit("_", 1)[-1] for p in _history(root)], ["2", "3"])

    def test_same_millisecond_archive_after_cleanup_keeps_newest(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = 1_700_000_000.0

            def run(key, **kwargs):
                rec = MatchDiagnostics(root, "BT", now=now, **kwargs)
                rec.record(key, 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
                rec.finalize(False, "monitor_start")
                wait_for_history()

            run("r1")
            one = sum(f.stat().st_size for f in _history(root)[0].iterdir())
            limit_mb = (one * 2.5) / (1024 * 1024)
            for key in ("r2", "r3", "r4"):
                run(key, history_max_mb=limit_mb)
            keys = [json.loads((r / "summary.json").read_text(encoding="utf-8"))["records"][0]["key"]
                    for r in _history(root)]
            self.assertEqual(sorted(keys), ["r3", "r4"])

    def test_interrupted_archive_leaves_no_half_archive_and_stale_staging_is_swept(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            tmp_base = root / "_captures" / "match_diagnostics" / "BT" / "history_tmp"
            stale = tmp_base / "dead"
            stale.mkdir(parents=True)
            (stale / "01_x.jpg").write_bytes(b"x")
            old = time.time() - match_diagnostics.STAGING_STALE_SEC - 60
            os.utime(str(stale), (old, old))
            # Another process (different device) is mid-write right now: its staging must survive our cleanup.
            live = root / "_captures" / "match_diagnostics" / "DFU-01" / "history_tmp" / "live"
            live.mkdir(parents=True)
            (live / "01_y.jpg").write_bytes(b"y")
            rec = MatchDiagnostics(root, "BT")
            rec.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            real_dump = match_diagnostics.json.dump

            def dump(obj, handle, **kwargs):
                if "history_tmp" in str(handle.name):
                    raise OSError("exit mid-write")
                return real_dump(obj, handle, **kwargs)

            with mock.patch("match_diagnostics.json.dump", side_effect=dump):
                rec.finalize(True, "check")
                wait_for_history()
            self.assertEqual(_history(root), [])
            self.assertFalse(stale.exists())
            self.assertTrue((live / "01_y.jpg").exists())

    def test_other_process_mid_write_survives_our_cleanup(self):
        import subprocess
        child = r"""
import sys, time, json
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0, sys.argv[1])
import match_diagnostics as md
flag_ready, flag_go = Path(sys.argv[3]), Path(sys.argv[4])
real = md.json.dump
def dump(obj, handle, **kw):
    if "history_tmp" in str(handle.name):
        flag_ready.write_text("x")
        for _ in range(300):
            if flag_go.exists():
                break
            time.sleep(0.05)
    return real(obj, handle, **kw)
md.json.dump = dump
rec = md.MatchDiagnostics(sys.argv[2], "DFU-01")
rec.record("child", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
rec.finalize(True, "input")
md.wait_for_history()
"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            ready, go = Path(temp) / "ready", Path(temp) / "go"
            proc = subprocess.Popen([sys.executable, "-c", child, str(Path(__file__).resolve().parents[1] / "host-app"),
                                     str(root), str(ready), str(go)])
            try:
                deadline = time.monotonic() + 30
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.exists())
                # This process archives for FCT-01 and sweeps while DFU-01's child is mid-write.
                other = MatchDiagnostics(root, "FCT-01")
                other.record("parent", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
                other.finalize(True, "focus")
                wait_for_history()
            finally:
                go.write_text("x")
                proc.wait(timeout=30)
            self.assertEqual(proc.returncode, 0)
            runs = list((root / "_captures" / "match_diagnostics" / "DFU-01" / "history").glob("*/*"))
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "summary.json").exists())

    def _two_summaries_kept(self, root):
        import json
        runs = _history(root)
        keys = sorted(json.loads((r / "summary.json").read_text(encoding="utf-8"))["records"][0]["key"] for r in runs)
        return keys

    def test_day_folder_removed_by_other_process_before_listing_is_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = 1_700_000_000.0
            _run(root, "first", True, now + 5)  # a different millisecond, same day
            real_iterdir = Path.iterdir
            state = {"done": False}

            def iterdir(path):
                if not state["done"] and path.parent.name == "history" and path.parent.parent.name == "BT":
                    state["done"] = True
                    # Another process's cleanup empties and removes the day folder right after our mkdir.
                    for run in real_iterdir(path):
                        shutil.rmtree(str(run))
                    path.rmdir()
                return real_iterdir(path)

            import shutil
            rec = MatchDiagnostics(root, "BT", now=now)
            rec.record("second", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            with mock.patch.object(Path, "iterdir", iterdir):
                rec.finalize(True, "check")
                wait_for_history()
            self.assertTrue(state["done"])
            self.assertEqual(self._two_summaries_kept(root), ["second"])

    def test_two_publishers_choosing_the_same_name_both_keep_their_archive(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            now = 1_700_000_000.0
            real_rename = os.rename
            state = {"done": False}

            def rename(src, dst):
                if not state["done"] and "history_tmp" in src:
                    state["done"] = True
                    # Another process publishes a complete archive under the very same name first.
                    Path(dst).mkdir(parents=True)
                    (Path(dst) / "summary.json").write_text(
                        json.dumps({"records": [{"key": "other_process"}]}), encoding="utf-8")
                return real_rename(src, dst)

            rec = MatchDiagnostics(root, "BT", now=now)
            rec.record("ours", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
            with mock.patch("match_diagnostics.os.rename", side_effect=rename):
                rec.finalize(False, "monitor_start")
                wait_for_history()
            self.assertTrue(state["done"])
            self.assertEqual(self._two_summaries_kept(root), ["other_process", "ours"])

    def test_idle_worker_does_not_keep_raw_frames_alive(self):
        import gc
        import weakref
        refs = []

        def fake(self, payload, annotated, ok, operation, stamp):
            refs.append(weakref.ref(annotated[1][1]))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            rec = MatchDiagnostics(root, "BT")
            rec.record("step", 0.9, 0.8, np.zeros((1080, 1920, 3), dtype=np.uint8), (1, 1, 5, 5))
            with mock.patch.object(MatchDiagnostics, "_archive_and_cleanup", fake):
                rec.finalize(True, "check")
                deadline = time.monotonic() + 5
                while match_diagnostics._pending["jobs"] and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertEqual(match_diagnostics._pending["jobs"], 0)
            rec._annotated = {}
            gc.collect()
            self.assertIsNone(refs[0]())

    def test_latest_png_write_returning_false_clears_summary_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            rec = MatchDiagnostics(root, "BT")
            real = cv2.imwrite
            with mock.patch("match_diagnostics.cv2.imwrite",
                            side_effect=lambda path, *a: False if str(path).endswith(".png") else real(path, *a)):
                with self.assertLogs("match_diagnostics", level="WARNING"):
                    rec.record("step", 0.9, 0.8, np.zeros((20, 20, 3), dtype=np.uint8), (1, 1, 5, 5))
                rec.finalize(True, "check")
                wait_for_history()
            self.assertEqual(MatchDiagnostics.load(root, "BT")["records"][0]["image"], "")
            self.assertEqual(sorted(p.name for p in _history(root)[0].iterdir()), ["01_step.jpg", "summary.json"])


class ConfirmWriteFailureTests(unittest.TestCase):
    def test_confirm_record_write_failure_keeps_original_flow_error(self):
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from hid_session import FlowError
        from protocol_helpers import LogSolutionKVM
        from template_catalog import TemplateCatalog
        import monitor_flow

        class Gate:
            stats = None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "template"
            seed = LogSolutionKVM()
            frame = seed.latest_frame().image
            x, y, w, h = seed.dock_icon_box()
            TemplateCatalog(root).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h), "COMMON",
                                            "log_solution_dock_icon")
            kvm = LogSolutionKVM(state="standby")
            kvm.close_window()
            real = cv2.imwrite

            def boom(path, *args):
                if "confirm_last_unreliable" in str(path):
                    raise OSError("disk full")
                return real(path, *args)

            with mock.patch("match_diagnostics.cv2.imwrite", side_effect=boom):
                with self.assertRaises(FlowError) as raised:
                    asyncio.run(monitor_flow.run_monitor_start(
                        kvm, Gate(), "DFU-01", template_root=root, confirm_sec=0.1, start_timeout_sec=0.1,
                        log=lambda *_: None))
            wait_for_history()
            self.assertEqual(raised.exception.code, "log_solution_not_found")
            self.assertIn("last reading", str(raised.exception))


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


# These locks are simulated on every platform; no real Windows file handles are needed.


def _reset_error(kind):
    if kind == "winerror32":
        error = PermissionError(13, "sharing violation: \u6a94\u6848\u4f7f\u7528\u4e2d")
        error.winerror = 32
        return error
    if kind == "permission":
        return PermissionError(13, "access denied")
    return OSError(16, "resource busy")


@pytest.mark.parametrize("kind", ["permission", "winerror32", "oserror"])
@pytest.mark.parametrize("outcome", ["retry", "rename", "isolated"])
def test_latest_reset_lock_preserves_new_round(tmp_path, monkeypatch, caplog, kind, outcome):
    root = tmp_path / "template"
    old = _run(root, "old", False, time.time())
    canonical = old.directory
    stale_summary = (canonical / "summary.json").read_bytes()
    real_rmtree = match_diagnostics.shutil.rmtree
    real_rename = Path.rename
    calls, sleeps = [], []

    def rmtree(path, *args, **kwargs):
        if Path(path) == canonical:
            calls.append(path)
            if outcome != "retry" or len(calls) == 1:
                raise _reset_error(kind)
        return real_rmtree(path, *args, **kwargs)

    def rename(path, target):
        if path == canonical and outcome == "isolated":
            raise _reset_error(kind)
        return real_rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(match_diagnostics.shutil, "rmtree", rmtree)
        patch.setattr(Path, "rename", rename)
        patch.setattr(match_diagnostics.time, "sleep", sleeps.append)
        recorder = MatchDiagnostics(root, "BT")
        assert recorder.records == []
        assert MatchDiagnostics.load(root, "BT") is None
        assert list(recorder.directory.iterdir()) == []
        recorder.record("step", 0.95, 0.8, np.full((60, 80, 3), 255, dtype=np.uint8), (1, 1, 10, 10))
        recorder.record("next", 0.95, 0.8, np.zeros((60, 80, 3), dtype=np.uint8), (1, 1, 10, 10))
        recorder.finalize(True, "monitor_start")
        assert wait_for_history()

    assert len(calls) == (2 if outcome == "retry" else 3)
    assert sleeps == ([0.05] if outcome == "retry" else [0.05, 0.10])
    latest = MatchDiagnostics.load(root, "BT")
    assert latest["ok"] is True
    assert [r["key"] for r in latest["records"]] == ["step", "next"]
    assert MatchDiagnostics.latest_directory(root, "BT") == recorder.directory
    for record in latest["records"]:
        assert cv2.imread(str(recorder.directory / record["image"])) is not None
    assert len(_history(root)) == 2
    if outcome == "isolated":
        assert (canonical / "summary.json").read_bytes() == stale_summary
        assert recorder.directory != canonical
    assert all(record.getMessage().isascii() for record in caplog.records)
    assert any("sharing violation" in record.getMessage() for record in caplog.records)
    if kind == "winerror32":
        assert any("winerror=32" in record.getMessage() for record in caplog.records)
    # Once the lock goes away, normal layout and readers recover on the next round.
    following = MatchDiagnostics(root, "BT")
    assert following.directory == canonical
    assert MatchDiagnostics.load(root, "BT") is None
    following.record("fresh", None, 0.8)
    following.finalize(True, "check")
    assert wait_for_history()
    assert [r["key"] for r in MatchDiagnostics.load(root, "BT")["records"]] == ["fresh"]


@pytest.mark.parametrize("failure", [None, "mkdir", "write", "replace", "mkdir_replace"])
@pytest.mark.parametrize("winerror", [32, 5])
@pytest.mark.parametrize("rename_locked", [False, True])
def test_monitor_start_continues_after_winerror32_reset(tmp_path, monkeypatch, rename_locked, failure, winerror):
    import asyncio
    from types import SimpleNamespace
    from protocol_helpers import LogSolutionKVM
    from log_solution_reader import MarkerState
    import monitor_flow

    canonical = MatchDiagnostics(tmp_path, "DFU-01").directory
    (canonical / "summary.json").write_text("stale invalid JSON", encoding="ascii")
    (canonical.parent / "latest_directory.txt").write_text("latest", encoding="ascii")
    real_rmtree, real_rename = match_diagnostics.shutil.rmtree, Path.rename
    from template_catalog import TemplateCatalog
    seed = LogSolutionKVM()
    frame = seed.latest_frame().image
    x, y, w, h = seed.dock_icon_box()
    TemplateCatalog(tmp_path).save_crop(frame[y:y + h, x:x + w].copy(), (0, 0, w, h),
                                      "COMMON", "log_solution_dock_icon")

    def rmtree(path, *args, **kwargs):
        if Path(path) == canonical:
            raise _reset_error("winerror32")
        return real_rmtree(path, *args, **kwargs)

    def rename(path, target):
        if path == canonical and rename_locked:
            raise _reset_error("winerror32")
        return real_rename(path, target)

    async def inline_thread(function, *args):
        return function(*args)

    with monkeypatch.context() as patch:
        if failure is not None:
            _inject_publish_error(patch, failure, winerror)
        patch.setattr(monitor_flow.asyncio, "to_thread", inline_thread)
        patch.setattr(match_diagnostics.shutil, "rmtree", rmtree)
        patch.setattr(Path, "rename", rename)
        patch.setattr(match_diagnostics.time, "sleep", lambda _: None)
        patch.setattr(LogSolutionKVM, "is_healthy", lambda _: True)
        patch.setattr(monitor_flow, "observe_latest_round_frame", lambda *_: SimpleNamespace(
            kind="reading", state=MarkerState.MONITORING, capacity=10))
        result = asyncio.run(monitor_flow.run_monitor_start(
            LogSolutionKVM(state="standby"), SimpleNamespace(stats=None), "DFU-01", template_root=tmp_path))
        assert result == {"state": "monitoring", "capacity": 10}
        assert wait_for_history()
    import json
    summaries = list(canonical.parent.glob("*/summary.json"))
    summary = next(json.loads(path.read_text()) for path in summaries
                   if path.read_text() != "stale invalid JSON")
    assert summary["ok"] is True
    assert summary["operation"] == "monitor_start"


def _inject_publish_error(patch, failure, winerror):
    real_mkdir, real_write, real_replace = Path.mkdir, Path.write_text, match_diagnostics.os.replace

    def denied():
        error = PermissionError(13, "拒絕存取：私人路徑")
        error.winerror = winerror
        return error

    def mkdir(path, *args, **kwargs):
        if path.name == "latest" or (failure == "all_mkdir" and path.name.startswith("latest_round_")):
            raise denied()
        return real_mkdir(path, *args, **kwargs)

    def write(path, *args, **kwargs):
        if path.name.startswith("latest_selector_"):
            real_write(path, "partial", encoding="ascii")
            raise denied()
        return real_write(path, *args, **kwargs)

    def replace(src, dst):
        if Path(src).name.startswith("latest_selector_"):
            raise denied()
        return real_replace(src, dst)

    if failure in ("mkdir", "all_mkdir", "mkdir_replace"):
        patch.setattr(Path, "mkdir", mkdir)
    if failure == "write":
        patch.setattr(Path, "write_text", write)
    if failure in ("replace", "mkdir_replace"):
        patch.setattr(match_diagnostics.os, "replace", replace)


@pytest.mark.parametrize("winerror", [32, 5])
@pytest.mark.parametrize("failure", ["mkdir", "all_mkdir", "write", "replace", "mkdir_replace"])
def test_latest_publish_errors_do_not_escape(tmp_path, monkeypatch, caplog, failure, winerror):
    parent = tmp_path / "_captures" / "match_diagnostics" / "BT"
    parent.mkdir(parents=True)
    (parent / "latest_directory.txt").write_text("latest", encoding="ascii")
    with monkeypatch.context() as patch:
        _inject_publish_error(patch, failure, winerror)
        recorder = MatchDiagnostics(tmp_path, "BT")
    assert recorder.records == []
    if failure in ("mkdir", "mkdir_replace"):
        assert recorder.directory.name.startswith("latest_round_")
        assert recorder.directory.is_dir()
    assert not list(parent.glob("latest_selector_*"))
    assert caplog.records
    assert all(record.getMessage().isascii() for record in caplog.records)
    assert any(f"winerror={winerror}" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("rename_locked", [False, True])
def test_latest_cleanup_across_locked_rounds(tmp_path, monkeypatch, caplog, rename_locked):
    canonical = MatchDiagnostics(tmp_path, "BT").directory
    real_rmtree, real_rename = match_diagnostics.shutil.rmtree, Path.rename

    def rmtree(path, *args, **kwargs):
        if Path(path) == canonical:
            raise _reset_error("winerror32")
        return real_rmtree(path, *args, **kwargs)

    def rename(path, target):
        if path == canonical and rename_locked:
            raise _reset_error("winerror32")
        return real_rename(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(match_diagnostics.shutil, "rmtree", rmtree)
        patch.setattr(Path, "rename", rename)
        patch.setattr(match_diagnostics.time, "sleep", lambda _: None)
        for _ in range(2):
            current = MatchDiagnostics(tmp_path, "BT")
            (current.directory / "frame.png").write_bytes(b"round pixels")
            leftovers = list(canonical.parent.glob("latest_round_*")) + list(canonical.parent.glob("latest_locked_*"))
            assert leftovers == ([current.directory] if rename_locked else [])
            assert (current.directory / "frame.png").read_bytes() == b"round pixels"
    recovered = MatchDiagnostics(tmp_path, "BT")
    assert recovered.directory == canonical
    if rename_locked:
        assert (canonical.parent / "latest_directory.txt").read_text(encoding="ascii") == "latest"
    assert not list(canonical.parent.glob("latest_round_*"))
    assert not list(canonical.parent.glob("latest_locked_*"))
    assert all(record.getMessage().isascii() for record in caplog.records)


def test_obsolete_round_cleanup_failure_is_ascii_and_retried(tmp_path, monkeypatch, caplog):
    canonical = MatchDiagnostics(tmp_path, "BT").directory
    locked = canonical.with_name("latest_locked_old")
    locked.mkdir()
    (locked / "frame.png").write_bytes(b"locked pixels")
    real_rmtree = match_diagnostics.shutil.rmtree
    attempts = []

    def rmtree(path, *args, **kwargs):
        if Path(path) == locked:
            attempts.append(path)
            raise _reset_error("winerror32")
        return real_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(match_diagnostics.shutil, "rmtree", rmtree)
        for _ in range(2):
            assert MatchDiagnostics(tmp_path, "BT").directory == canonical
    assert len(attempts) == 2
    assert (locked / "frame.png").exists()
    assert any("obsolete round cleanup failed" in record.getMessage() for record in caplog.records)
    assert all(record.getMessage().isascii() for record in caplog.records)
    MatchDiagnostics(tmp_path, "BT")
    assert not locked.exists()
