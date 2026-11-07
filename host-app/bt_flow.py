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


def start_all_region(frame, window):
    """Where Start All may be: right of and below the window match, to the frame edge.

    The window template is usually only the title-bar corner (traffic lights), so its width says nothing
    about the window's width. Start All sits at the bottom centre of the BT window.
    """
    height, width = frame.shape[:2]
    return window.left, window.top, width - window.left, height - window.top


def find_start_all(session, template, frame, window, suffix=""):
    """Start All right of / below the window corner; if that misses, the whole frame (the corner may have
    matched another window's traffic lights). Raises ``FlowError(hid_failed)`` with the better score."""
    try:
        return session.find(
            "start_all" + suffix, template, frame, within=start_all_region(frame, window), downscale=False,
            message="Start All not found (similarity {value:.2f})",
        )
    except FlowError as exc:
        if exc.score is None:
            raise
        first = exc.score
    try:
        return session.find(
            "start_all_full_frame" + suffix, template, frame, downscale=False,
            message="Start All not found (similarity {value:.2f})",
        )
    except FlowError as exc:
        if exc.score is not None and exc.score < first:
            exc.args = (f"Start All not found (similarity {first:.2f})",)
            exc.score = first
        raise


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
            raise FlowError("hid_failed", "Missing BT templates (dock_icon / window / start_all)")
        frame = session.frame()
        await session.focus_dock(
            "dock_icon", dock, frame,
            message="Dock icon not found (similarity {value:.2f})",
            settle=DOCK_SETTLE_DELAY,
            after=lambda: session.park_pointer(frame),  # a Dock tooltip must not cover Start All
        )
        frame = await session.wait_fresh_frame()

        def locate_start_all(image, suffix):
            found = session.find(
                "window" + suffix, window, image, downscale=True, color=(0, 255, 0),
                message="BT window not found (similarity {value:.2f})",
            )
            return find_start_all(session, start_all, image, found, suffix)

        # One retry on fresh frames; the Dock is never clicked twice.
        frame, start = await session.match_with_retry(frame, locate_start_all)
        await session.click(start, failure="Start All HID failed", settle=0,
                            after=lambda: session.park_pointer(frame))
        ok = True
        return {"action": "start_all"}
    finally:
        diagnostics.finalize(ok, "start_all")
