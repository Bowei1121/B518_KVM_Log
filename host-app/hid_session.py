"""Find a template and send HID through one session.

Flows only sequence steps. Matching, diagnostics, and ``rpc_errors`` stay here.
"""

import asyncio
import json
import time

import cv2

from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog


SETTINGS_DEVICE = "COMMON"
SETTINGS_FILE = "hid_settings.json"
MAX_COORD = 20000
FRESH_FRAME_TIMEOUT_SEC = 2.0  # upper bound of the wait for post-park frames (hid_settings.json: fresh_frame_timeout_sec)
FRESH_FRAME_COUNT = 2  # frames that must arrive after the pointer was parked
FRESH_POLL_SEC = 0.02
FRESH_FALLBACK_SEC = 0.5  # KVMs that report no frame sequence: fixed wait instead of counting frames


def neutral_pointer_position(frame):
    """Where the pointer rests after a click: top edge, mid-screen.

    Off the Dock (no hover magnification or program-name tooltip over the icon), off the Log Solution
    band, and away from the screen corners (macOS Hot Corners). With "auto-hide the menu bar" the top
    edge reveals the menu bar: override with ``pointer_park`` in ``COMMON/hid_settings.json``.
    """
    return frame.shape[1] // 2, 1


class FlowError(Exception):
    """A refused or failed station flow. ``code`` is a protocol error code.

    ``entered`` lists DFU slots typed so far. ``score`` is set when a template
    match missed, so a caller can turn that into a different error code.
    """

    def __init__(self, code, message, entered=()):
        super().__init__(message)
        self.code = code
        self.entered = sorted(entered)
        self.score = None


class Box:
    """A match in frame pixels. Iterates as ``(left, top, width, height)``."""

    def __init__(self, left, top, width, height, score):
        self.left = int(left)
        self.top = int(top)
        self.width = int(width)
        self.height = int(height)
        self.score = score

    def __iter__(self):
        yield self.left
        yield self.top
        yield self.width
        yield self.height

    def center(self):
        return int(self.left + self.width / 2), int(self.top + self.height / 2)


def load_gray(path):
    img = cv2.imread(path)
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def locate(gray, tmpl, downscale=True):
    """回傳 (相似度, left, top, w, h); 找不到 left=None。座標為 gray 內像素。
    大目標(視窗)用 downscale=True 粗搜尋加速; 小元件用 False 全解析度避免誤判。"""
    height, width = gray.shape[:2]
    th, tw = tmpl.shape[:2]
    scale = 0.5 if downscale else 1.0
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1.0 else gray

    coarse = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
    best_value, best_loc, best_scale = -1.0, None, None
    for factor in coarse:
        nw, nh = int(tw * factor * scale), int(th * factor * scale)
        if nw < 8 or nh < 8 or nw > small.shape[1] or nh > small.shape[0]:
            continue
        resized = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(small, resized, cv2.TM_CCOEFF_NORMED)
        _, peak, _, location = cv2.minMaxLoc(result)
        if peak > best_value:
            best_value, best_loc, best_scale = peak, location, factor
    if best_loc is None:
        return -1.0, None, None, None, None

    fx, fy = best_loc[0] / scale, best_loc[1] / scale
    margin = int(max(tw, th) * best_scale * 0.25) + 10
    x0, y0 = max(0, int(fx - margin)), max(0, int(fy - margin))
    x1 = min(width, int(fx + tw * best_scale + margin))
    y1 = min(height, int(fy + th * best_scale + margin))
    roi = gray[y0:y1, x0:x1]

    refined = (best_value, None, None, None, None)
    for factor in (0.85, 0.92, 1.0, 1.08, 1.15):
        rs = best_scale * factor
        nw, nh = int(tw * rs), int(th * rs)
        if nw < 8 or nh < 8 or nw > roi.shape[1] or nh > roi.shape[0]:
            continue
        resized = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
        _, peak, _, location = cv2.minMaxLoc(result)
        if peak > refined[0]:
            refined = (peak, x0 + location[0], y0 + location[1], nw, nh)
    if refined[1] is None:
        return best_value, int(fx), int(fy), int(tw * best_scale), int(th * best_scale)
    return refined


class HidSession:
    """``kvm`` plus the template catalog and the run's diagnostics recorder.

    Each command builds its own session. Construction drops HID errors left by
    the previous command. Errors that arrive during this command are reported.
    """

    def __init__(self, kvm, template_root, diagnostics, *, threshold=0.8, log=print):
        self.kvm = kvm
        self.catalog = TemplateCatalog(template_root)
        self.diagnostics = diagnostics
        self.threshold = threshold
        self.log = log
        errors = getattr(kvm, "rpc_errors", None)
        if errors is not None:
            errors.clear()

    def load(self, device, key, *, record_missing=True):
        """Gray template, or None. Missing files are logged and recorded unless ``record_missing`` is false."""
        path = self.catalog.path(device, key)
        image = load_gray(str(path))
        if image is None and record_missing:
            en = f"Template file missing: {path}"
            zh = f"讀不到模板，期待檔案: {path}"
            self.log(en, zh)
            if self.diagnostics:
                self.diagnostics.record(key, None, self.threshold, note={"en": en, "zh": zh})
        return image

    def settings(self):
        """``COMMON/hid_settings.json`` as a dict (empty when absent), read and validated once per session.

        Flows call this before their first HID action, so an invalid file fails before anything is sent.
        ``pointer_park``: ``[x, y]`` screen pixels where the pointer rests after a click.
        ``fresh_frame_timeout_sec``: positive number, upper bound of the wait for fresh frames.
        """
        cached = getattr(self, "_settings", None)
        if cached is not None:
            return cached
        path = self.catalog.device_dir(SETTINGS_DEVICE) / SETTINGS_FILE
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                if "pointer_park" in data:
                    park = data["pointer_park"]
                    ok = (isinstance(park, list) and len(park) == 2
                          and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_COORD
                                  for v in park))
                    if not ok:
                        raise ValueError("pointer_park must be [x, y] integers")
                if "fresh_frame_timeout_sec" in data:
                    timeout = data["fresh_frame_timeout_sec"]
                    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
                        raise ValueError("fresh_frame_timeout_sec must be a number in (0, 60]")
            except (OSError, ValueError) as exc:
                raise FlowError("hid_failed", f"Invalid {SETTINGS_FILE}: {exc}")
        self._settings = data
        return data

    async def park_pointer(self, frame=None):
        """Move the pointer to the park position; remember the time and the newest frame sequence.

        Default ``neutral_pointer_position``; ``pointer_park`` in the settings file overrides it (clamped to
        the frame). The move is best effort: a failed move (RPC error or exception) is noted in the
        diagnostics and never turns an already successful click into a failure. A KVM without ``move``
        only records the time.
        """
        if frame is None:
            frame = self.frame()
        park = self.settings().get("pointer_park")
        x, y = neutral_pointer_position(frame) if park is None else park
        height, width = frame.shape[:2]
        x, y = min(max(int(x), 0), width - 1), min(max(int(y), 0), height - 1)
        if hasattr(self.kvm, "move"):
            errors = getattr(self.kvm, "rpc_errors", None)
            before = len(errors) if errors is not None else 0
            problem = None
            try:
                await self.kvm.move(x, y)
            except Exception as exc:  # noqa: BLE001 - the click already happened
                problem = str(exc) or type(exc).__name__
            if errors is not None and len(errors) > before:
                problem = problem or str(errors[-1])
                del errors[before:]  # belongs to the park, not to the click that came before
            if problem and self.diagnostics:
                self.diagnostics.record("park_failed", None, self.threshold, note={
                    "en": f"Moving the pointer to ({x}, {y}) failed: {problem}; continuing",
                    "zh": f"移開滑鼠到 ({x}, {y}) 失敗：{problem}；繼續執行"})
        self.park_mark = time.monotonic()
        base = self.kvm.latest_frame()
        sequence = getattr(base, "sequence", None)
        self.park_base = (getattr(base, "stream_id", None), sequence) if isinstance(sequence, int) else None
        return self.park_mark

    async def wait_fresh_frame(self, suffix="", *, since=None, timeout=None):
        """Frame image received after the pointer was parked (needs at least FRESH_FRAME_COUNT of them).

        Frames that arrived between the park and this call count: the baseline is the newest frame at the
        park. ``since`` defaults to the last ``park_pointer``; ``timeout`` caps the configured wait. On
        timeout the newest frame is used and a ``fresh_frame_timeout<suffix>`` note goes to the diagnostics.
        A KVM without frame sequence numbers waits a fixed FRESH_FALLBACK_SEC instead.
        """
        since = getattr(self, "park_mark", None) if since is None else since
        if since is None:
            return self.frame()
        configured = float(self.settings().get("fresh_frame_timeout_sec", FRESH_FRAME_TIMEOUT_SEC))
        timeout = configured if timeout is None else max(0.0, min(configured, timeout))
        base = getattr(self, "park_base", None)
        deadline = time.monotonic() + timeout
        first_sequence = None
        while True:
            snapshot = self.kvm.latest_frame()
            sequence = getattr(snapshot, "sequence", None)
            received = getattr(snapshot, "received_monotonic", None)
            if snapshot is not None and isinstance(sequence, int) and isinstance(received, (int, float)):
                if received > since:
                    if base is not None and base[0] == getattr(snapshot, "stream_id", None):
                        if sequence - base[1] >= FRESH_FRAME_COUNT:
                            return snapshot.image
                    else:  # no baseline or the stream changed: count from the first frame seen after the park
                        if first_sequence is None:
                            first_sequence = sequence
                        if sequence - first_sequence >= FRESH_FRAME_COUNT - 1:
                            return snapshot.image
            elif snapshot is not None:
                await asyncio.sleep(min(FRESH_FALLBACK_SEC, timeout))
                return self.frame()
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(FRESH_POLL_SEC)
        if self.diagnostics:
            self.diagnostics.record("fresh_frame_timeout" + suffix, None, self.threshold, note={
                "en": f"No {FRESH_FRAME_COUNT} frames received after the pointer was parked within {timeout:g}s; "
                      "continuing with the newest frame",
                "zh": f"移開滑鼠後 {timeout:g} 秒內未收到 {FRESH_FRAME_COUNT} 張新影格，改用當下最新影格繼續"})
        return self.frame()

    async def match_with_retry(self, frame, match):
        """``(frame, result)`` of ``match(frame, "")``; one retry on a missed match, never a second Dock click.

        ``match(frame, suffix)`` must only look (no HID): the retry parks the pointer again, waits for fresh
        frames and matches once more with suffix ``_retry`` (diagnostics keys). Both missed: the error with
        the higher score. Errors other than ``hid_failed`` pass through.
        """
        try:
            return frame, match(frame, "")
        except FlowError as first:
            if first.code != "hid_failed":
                raise
            await self.park_pointer(frame)
            frame = await self.wait_fresh_frame("_retry")
            try:
                return frame, match(frame, "_retry")
            except FlowError as second:
                if second.code != "hid_failed":
                    raise
                rank = lambda exc: -1.0 if exc.score is None else exc.score
                raise (first if rank(first) > rank(second) else second)

    def frame(self, missing="No JetKVM frame available"):
        snapshot = self.kvm.latest_frame()
        if snapshot is None:
            raise FlowError("hid_failed", missing)
        return snapshot.image

    def find(self, key, template, frame, *, within=None, downscale=False, color=(0, 200, 255),
             message=None, check_threshold=True):
        """Match ``template`` on ``frame``. ``within`` is an ROI in frame pixels.

        Records diagnostics. Raises ``FlowError("hid_failed")`` when the match misses.
        ``message`` is a format string (``value``, ``key``, ``threshold``) or a fixed sentence.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        origin_x, origin_y = 0, 0
        if within is not None:
            origin_x, origin_y, roi_w, roi_h = within
            gray = gray[origin_y:origin_y + roi_h, origin_x:origin_x + roi_w]
        value, left, top, width, height = locate(gray, template, downscale=downscale)
        if left is not None:
            left += origin_x
            top += origin_y
        box = (left, top, width, height) if left is not None else None
        if self.diagnostics:
            self.diagnostics.record(key, value, self.threshold, frame, box, color=color)
        missed = box is None or (check_threshold and value < self.threshold)
        if missed:
            text = f"{key} not found (similarity {value:.2f})" if message is None else message.format(
                value=value, key=key, threshold=self.threshold)
            error = FlowError("hid_failed", text)
            error.score = value
            raise error
        return Box(left, top, width, height, value)

    def last_hid_error(self):
        errors = getattr(self.kvm, "rpc_errors", [])
        return errors[-1] if errors else None

    def _raise_if_hid(self, failure, entered=()):
        error = self.last_hid_error()
        if error:
            errors = getattr(self.kvm, "rpc_errors", None)
            if errors is not None:
                errors.clear()
            raise FlowError("hid_failed", f"{failure}: {error}", entered)

    async def click(self, box, *, failure, settle=0.0, after=None, entered=()):
        """Raise a leftover HID error, then clear, click the box center, and raise ``FlowError("hid_failed")``."""
        self._raise_if_hid(failure, entered)
        errors = getattr(self.kvm, "rpc_errors", None)
        if errors is not None:
            errors.clear()
        if isinstance(box, Box):
            point = box.center()
        else:
            left, top, width, height = box
            point = int(left + width / 2), int(top + height / 2)
        await self.kvm.click(*point)
        if after is not None:
            await after()
        if settle:
            await asyncio.sleep(settle)
        self._raise_if_hid(failure, entered)

    async def focus_dock(self, key, template, frame, *, message, failure="Dock HID failed", settle=0.0, after=None):
        """Find ``key`` on the full frame and click it."""
        box = self.find(key, template, frame, downscale=False, message=message)
        await self.click(box, failure=failure, settle=settle, after=after)
        return box

    async def focus_dock_fresh(self, key, template, frame, *, message, failure="Dock HID failed", settle=0.0,
                               within_sec=None):
        """The one Dock sequence: validate the settings, click the icon, park the pointer, wait for fresh frames.

        Returns ``(box, fresh_image)``. ``dock_clicked_at`` is set when the click went out; ``within_sec``
        caps the whole wait to that many seconds after the click (Monitor's 3 s confirm window).
        """
        self.settings()  # an invalid settings file fails before the first HID action

        async def after():
            self.dock_clicked_at = time.monotonic()
            await self.park_pointer(frame)

        box = self.find(key, template, frame, downscale=False, message=message)
        await self.click(box, failure=failure, settle=settle, after=after)
        remaining = None if within_sec is None else within_sec - (time.monotonic() - self.dock_clicked_at)
        return box, await self.wait_fresh_frame(timeout=remaining)

    async def type_text(self, text, *, failure, entered=()):
        await self.kvm.type_text(text)
        self._raise_if_hid(failure, entered)

    async def press_enter(self, *, failure, entered=()):
        await self.kvm.press_enter()
        self._raise_if_hid(failure, entered)

    async def press_command_shift_m(self, *, failure, entered=(), after=None):
        await self.kvm.press_command_shift_m()
        if after is not None:
            await after()
        self._raise_if_hid(failure, entered)


async def run_focus(kvm, device, template_root=None, threshold=0.8, log=print):
    """Locate and click a device Dock icon, preserving a diagnostic record."""
    catalog = TemplateCatalog(template_root)
    try:
        device = catalog.normalize_device(device)
        dock_key = catalog.focus_template(device)
    except ValueError as exc:
        log("Invalid device for Dock focus", str(exc))
        return {"ok": False}
    diagnostics = MatchDiagnostics(catalog.root, device)
    session = HidSession(kvm, template_root, diagnostics, threshold=threshold, log=log)
    result = {"ok": False, "dock_icon": None}

    def finish():
        diagnostics.finalize(result["ok"], "focus")
        return result

    dock = session.load(device, dock_key)
    if dock is None:
        return finish()
    try:
        frame = session.frame()
    except FlowError:
        log("No JetKVM frame", "沒有影格 (確認 KVM 已連線且遠端有畫面)")
        diagnostics.record("frame", None, threshold, note={"en": "No JetKVM frame", "zh": "沒有 JetKVM 影格"})
        return finish()
    try:
        box = session.find(
            dock_key, dock, frame, downscale=False,
            message="Dock icon not found (similarity {value:.2f} < {threshold})")
    except FlowError as exc:
        result["dock_icon"] = exc.score
        log(exc.code, str(exc))
        return finish()
    result["dock_icon"] = box.score
    try:
        await session.click(box, failure="Dock HID failed", settle=0.5)
    except FlowError as exc:
        result["hid_error"] = session.last_hid_error()
        log(exc.code, str(exc))
        return finish()
    result["ok"] = True
    log("Dock focus done", "Dock 前景化完成")
    return finish()
