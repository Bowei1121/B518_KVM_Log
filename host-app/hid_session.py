"""Find a template and send HID through one session.

Flows only sequence steps. Matching, diagnostics, and ``rpc_errors`` stay here.
"""

import asyncio

import cv2

from match_diagnostics import MatchDiagnostics
from template_catalog import TemplateCatalog


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
    """``kvm`` plus the template catalog and the run's diagnostics recorder."""

    def __init__(self, kvm, template_root, diagnostics, *, threshold=0.8, log=print):
        self.kvm = kvm
        self.catalog = TemplateCatalog(template_root)
        self.diagnostics = diagnostics
        self.threshold = threshold
        self.log = log

    def load(self, device, key, *, record_missing=True):
        """Gray template, or None. Missing files are logged and recorded unless ``record_missing`` is false."""
        path = self.catalog.path(device, key)
        image = load_gray(str(path))
        if image is None and record_missing:
            note = f"讀不到模板，期待檔案: {path}"
            self.log(note)
            if self.diagnostics:
                self.diagnostics.record(key, None, self.threshold, note=note)
        return image

    def frame(self, missing="沒有 JetKVM 影格"):
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
            text = f"找不到 {key}（相似度 {value:.2f}）" if message is None else message.format(
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

    async def focus_dock(self, key, template, frame, *, message, failure="Dock HID 錯誤", settle=0.0, after=None):
        """Find ``key`` on the full frame and click it."""
        box = self.find(key, template, frame, downscale=False, message=message)
        await self.click(box, failure=failure, settle=settle, after=after)
        return box

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
        log(str(exc))
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
        log("沒有影格 (確認 KVM 已連線且遠端有畫面)")
        diagnostics.record("frame", None, threshold, note="沒有 JetKVM 影格")
        return finish()
    try:
        box = session.find(
            dock_key, dock, frame, downscale=False,
            message="找不到 Dock icon (相似度 {value:.2f} < {threshold})")
    except FlowError as exc:
        result["dock_icon"] = exc.score
        log(str(exc))
        return finish()
    result["dock_icon"] = box.score
    try:
        await session.click(box, failure="Dock HID 錯誤", settle=0.5)
    except FlowError as exc:
        result["hid_error"] = session.last_hid_error()
        log(str(exc))
        return finish()
    result["ok"] = True
    log("Dock 前景化完成")
    return finish()
