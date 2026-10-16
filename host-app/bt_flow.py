"""BT Start All for the start_test 1.0 command.

Focus the BT Dock icon, find the BT window, find Start All inside it, click once.
Any missing element or HID failure is refused as hid_failed; nothing else is clicked.
The caller checks that Log Solution is monitoring before this runs.
"""
import asyncio

import cv2

from auto_flow import _load_template, locate
from hid_helpers import center, last_hid_error
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
DOCK_SETTLE_DELAY = 0.5


class BtStartError(Exception):
    """A refused or failed Start All. ``code`` is a protocol error code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


async def run_start_all(kvm, template_root=None, threshold=MATCH_THRESHOLD, log=print, diagnostics_key="BT"):
    """Click BT Start All once. Returns ``{"action": "start_all"}`` or raises ``BtStartError``."""
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    ok = False
    try:
        dock = _load_template(catalog, "BT", "dock_icon", log, diagnostics, threshold)
        window = _load_template(catalog, "BT", "window", log, diagnostics, threshold)
        start_all = _load_template(catalog, "BT", "start_all", log, diagnostics, threshold)
        if dock is None or window is None or start_all is None:
            raise BtStartError("hid_failed", "缺少 BT 模板 (dock_icon / window / start_all)")
        if getattr(kvm, "frame", None) is None:
            raise BtStartError("hid_failed", "沒有 JetKVM 影格")
        if hasattr(kvm, "rpc_errors"):
            kvm.rpc_errors.clear()

        frame = kvm.frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dv, dl, dt, dw, dh = locate(gray, dock, downscale=False)
        diagnostics.record("dock_icon", dv, threshold, frame, (dl, dt, dw, dh) if dl is not None else None)
        if dl is None or dv < threshold:
            raise BtStartError("hid_failed", f"找不到 Dock icon（相似度 {dv:.2f}）")
        await kvm.click(*center(dl, dt, dw, dh))
        await asyncio.sleep(DOCK_SETTLE_DELAY)
        if last_hid_error(kvm):
            raise BtStartError("hid_failed", f"Dock HID 錯誤: {last_hid_error(kvm)}")

        frame = kvm.frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        wv, wl, wt, ww, wh = locate(gray, window, downscale=True)
        diagnostics.record("window", wv, threshold, frame,
                           (wl, wt, ww, wh) if wl is not None else None, color=(0, 255, 0))
        if wl is None or wv < threshold:
            raise BtStartError("hid_failed", f"找不到 BT 視窗（相似度 {wv:.2f}）")
        roi = gray[wt:gray.shape[0], wl:wl + ww]
        sv, sl, st, sw, sh = locate(roi, start_all, downscale=False)
        diagnostics.record("start_all", sv, threshold, frame,
                           (wl + sl, wt + st, sw, sh) if sl is not None else None)
        if sl is None or sv < threshold:
            raise BtStartError("hid_failed", f"找不到 Start All（相似度 {sv:.2f}）")
        await kvm.click(*center(wl + sl, wt + st, sw, sh))
        await asyncio.sleep(0)
        if last_hid_error(kvm):
            raise BtStartError("hid_failed", f"Start All HID 錯誤: {last_hid_error(kvm)}")
        ok = True
        return {"action": "start_all"}
    finally:
        diagnostics.finalize(ok, "start_all")
