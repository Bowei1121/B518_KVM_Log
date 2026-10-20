"""BT Start All for the start_test 1.0 command.

Focus the BT Dock icon, find the BT window, find Start All inside it, click once.
Any missing element or HID failure is refused as hid_failed; nothing else is clicked.
The caller checks that Log Solution is monitoring before this runs.
"""

from hid_session import FlowError, HidSession
from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog

MATCH_THRESHOLD = 0.80
DOCK_SETTLE_DELAY = 0.5


async def run_start_all(kvm, template_root=None, threshold=MATCH_THRESHOLD, log=print, diagnostics_key="BT"):
    """Click BT Start All once. Returns ``{"action": "start_all"}`` or raises ``FlowError``."""
    catalog = TemplateCatalog(template_root)
    diagnostics = MatchDiagnostics(catalog.root, diagnostics_key)
    session = HidSession(kvm, template_root, diagnostics, threshold=threshold, log=log)
    ok = False
    try:
        dock = session.load("BT", "dock_icon")
        window = session.load("BT", "window")
        start_all = session.load("BT", "start_all")
        if dock is None or window is None or start_all is None:
            raise FlowError("hid_failed", "缺少 BT 模板 (dock_icon / window / start_all)")
        frame = session.frame()
        await session.focus_dock(
            "dock_icon", dock, frame,
            message="找不到 Dock icon（相似度 {value:.2f}）",
            settle=DOCK_SETTLE_DELAY,
        )

        frame = session.frame()
        found = session.find(
            "window", window, frame, downscale=True, color=(0, 255, 0),
            message="找不到 BT 視窗（相似度 {value:.2f}）",
        )
        roi = (found.left, found.top, found.width, frame.shape[0] - found.top)
        start = session.find(
            "start_all", start_all, frame, within=roi, downscale=False,
            message="找不到 Start All（相似度 {value:.2f}）",
        )
        await session.click(start, failure="Start All HID 錯誤", settle=0)
        ok = True
        return {"action": "start_all"}
    finally:
        diagnostics.finalize(ok, "start_all")
