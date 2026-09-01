# -*- coding: utf-8 -*-
"""
自動流程模組 (模組化 jetkvm_auto.py)
=====================================
run_flow(kvm, device, template_root, ...): 用「已連線的 JetKVMClient」跑完整流程:
  取最新影格 -> cv2 兩層比對 (window 視窗 -> 視窗內 input/button)
  -> 點輸入框輸入 SN -> 點 OK。座標= 影格像素, 經 KVM 的 absMouseReport 換算。

供 ui_app.py 的「Switch」按鈕使用。
"""
import asyncio
import cv2
import numpy as np
from template_catalog import TemplateCatalog

try:
    from ocr_sn import read_lines
except Exception:
    def read_lines(_img):      # OCR 模組缺失時的退路
        return []

# SN 欄在「視窗寬度」中的比例範圍 (左, 右)。OCR 前用此範圍裁出 SN 那一格,
# 才不會把 group/slot 等其他欄一起讀進來。不同版面可調整。
SN_COL_FRAC = (0.22, 0.51)


def load_gray(path):
    img = cv2.imread(path)
    return None if img is None else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def locate(gray, tmpl, downscale=True):
    """回傳 (相似度, left, top, w, h); 找不到 left=None。座標為 gray 內像素。
    大目標(視窗)用 downscale=True 粗搜尋加速; 小元件用 False 全解析度避免誤判。"""
    H, W = gray.shape[:2]
    th, tw = tmpl.shape[:2]
    DS = 0.5 if downscale else 1.0
    small = cv2.resize(gray, None, fx=DS, fy=DS, interpolation=cv2.INTER_AREA) if DS != 1.0 else gray

    coarse = [0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
    c_val, c_loc, c_scale = -1.0, None, None
    for s in coarse:
        nw, nh = int(tw * s * DS), int(th * s * DS)
        if nw < 8 or nh < 8 or nw > small.shape[1] or nh > small.shape[0]:
            continue
        t = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
        r = cv2.matchTemplate(small, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, ml = cv2.minMaxLoc(r)
        if mx > c_val:
            c_val, c_loc, c_scale = mx, ml, s
    if c_loc is None:
        return -1.0, None, None, None, None

    fx, fy = c_loc[0] / DS, c_loc[1] / DS
    margin = int(max(tw, th) * c_scale * 0.25) + 10
    x0, y0 = max(0, int(fx - margin)), max(0, int(fy - margin))
    x1 = min(W, int(fx + tw * c_scale + margin))
    y1 = min(H, int(fy + th * c_scale + margin))
    roi = gray[y0:y1, x0:x1]

    best = (c_val, None, None, None, None)
    for k in (0.85, 0.92, 1.0, 1.08, 1.15):
        rs = c_scale * k
        nw, nh = int(tw * rs), int(th * rs)
        if nw < 8 or nh < 8 or nw > roi.shape[1] or nh > roi.shape[0]:
            continue
        t = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
        r = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, ml = cv2.minMaxLoc(r)
        if mx > best[0]:
            best = (mx, x0 + ml[0], y0 + ml[1], nw, nh)
    if best[1] is None:
        return c_val, int(fx), int(fy), int(tw * c_scale), int(th * c_scale)
    return best


def find_all(gray, tmpl, threshold=0.8):
    """在 gray 中找出「所有」符合的位置 (非只取最高分), 回傳 [(x, y, score), ...]
    座標為 gray 內左上角。用簡單 NMS 去除重疊的重複命中。"""
    th, tw = tmpl.shape[:2]
    if th > gray.shape[0] or tw > gray.shape[1]:
        return []
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    pts = sorted(((int(x), int(y), float(res[y, x])) for x, y in zip(xs, ys)),
                 key=lambda p: -p[2])
    kept = []
    for x, y, s in pts:
        if all(abs(x - kx) > tw * 0.5 or abs(y - ky) > th * 0.5 for kx, ky, _ in kept):
            kept.append((x, y, s))
    return kept


def _load_template(catalog, device, template_key, log):
    path = catalog.path(device, template_key)
    image = load_gray(str(path))
    if image is None:
        log(f"讀不到模板，期待檔案: {path}")
    return image


async def run_check(kvm, device, template_root=None, threshold=0.8, log=print, annotate_path=None):
    """check 指令: 找視窗 -> 若有 testing 模板則回 testing;
    否則由上而下找所有 pass / fail 模板，回每列結果。
    回傳 dict: {ok, window, testing(bool), rows:[(index, 'pass'/'fail'), ...]}"""
    catalog = TemplateCatalog(template_root)
    try:
        device = catalog.normalize_device(device)
    except ValueError as exc:
        log(str(exc))
        return {"ok": False}
    win_g = _load_template(catalog, device, "window", log)
    if win_g is None:
        return {"ok": False}
    if kvm.frame is None:
        log("沒有影格 (確認 KVM 已連線且遠端有畫面)")
        return {"ok": False}

    frame = kvm.frame.copy()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    wv, wl, wt, ww, wh = locate(gray, win_g, downscale=True)
    log(f"[視窗] 相似度 {wv:.2f}")
    if wl is None or wv < threshold:
        if annotate_path:
            cv2.imwrite(annotate_path, frame)
        log(f"找不到視窗 (相似度 {wv:.2f} < {threshold})")
        return {"ok": False, "window": wv}
    H = gray.shape[0]
    roi = gray[wt:H, wl:wl + ww]
    cv2.rectangle(frame, (wl, wt), (wl + ww, H - 1), (0, 255, 0), 2)

    # 1) 是否在測試中
    testing_g = _load_template(catalog, device, "testing", log)
    if testing_g is not None:
        tv, tl, tt, ttw, tth = locate(roi, testing_g, downscale=False)
        log(f"[Testing] 相似度 {tv:.2f}")
        if tl is not None and tv >= threshold:
            cv2.rectangle(frame, (wl + tl, wt + tt),
                          (wl + tl + ttw, wt + tt + tth), (0, 165, 255), 2)
            if annotate_path:
                cv2.imwrite(annotate_path, frame)
            return {"ok": True, "testing": True, "rows": []}

    # 2) 由上而下找所有 Pass / Fail
    pass_g = _load_template(catalog, device, "pass", log)
    fail_g = _load_template(catalog, device, "fail", log)
    if pass_g is None or fail_g is None:
        return {"ok": False}

    # 先找出所有 PASS/FAIL 的位置 (roi 座標, roi 與下方 SN 欄同origin=wt)
    marks = []  # (y_center, result, row_h)
    for tmpl, res_name, color in ((pass_g, "pass", (0, 200, 0)),
                                  (fail_g, "fail", (0, 0, 255))):
        th, tw = tmpl.shape[:2]
        for x, y, s in find_all(roi, tmpl, threshold):
            marks.append((y + th // 2, res_name, th))
            cv2.rectangle(frame, (wl + x, wt + y), (wl + x + tw, wt + y + th), color, 2)

    # SN 欄「一次 OCR」(取代每列各一次, 大幅加速); 且只 OCR marks 涵蓋的
    # Y 範圍以縮小偵測面積。sn_lines 的 y 換算回 roi 座標 (與 marks 同origin=wt)。
    sx0 = wl + int(SN_COL_FRAC[0] * ww)
    sx1 = wl + int(SN_COL_FRAC[1] * ww)
    sn_lines = []
    if marks:
        rh = max(m[2] for m in marks)
        y_lo = max(0, min(m[0] for m in marks) - rh)
        y_hi = min(roi.shape[0], max(m[0] for m in marks) + rh)
        cv2.rectangle(frame, (sx0, wt + y_lo), (sx1, wt + y_hi), (200, 200, 0), 1)
        for text, ly in read_lines(frame[wt + y_lo:wt + y_hi, sx0:sx1]):
            sn_lines.append((text, y_lo + ly))     # -> roi 座標

    def nearest_sn(yc, tol):
        best, bestd = "SN", tol
        for text, ly in sn_lines:
            d = abs(ly - yc)
            if d < bestd:
                bestd, best = d, text
        return best

    items = []
    for yc, res_name, row_h in marks:
        items.append((yc, res_name, nearest_sn(yc, max(row_h, 15))))
    items.sort(key=lambda it: it[0])      # 由上而下
    rows = [(i + 1, r, sn) for i, (yc, r, sn) in enumerate(items)]
    log(f"[結果] {rows}")

    if annotate_path:
        cv2.imwrite(annotate_path, frame)
    return {"ok": True, "testing": False, "rows": rows}


async def run_flow(kvm, device, template_root=None, sn_text="SN_ABC", threshold=0.8,
                   log=print, annotate_path=None, mode="both", do_action=True):
    """用已連線的 kvm (JetKVMClient) 跑流程。
    mode: "input"=只找輸入框並輸入; "button"=只找按鈕並點擊;
          "both"=兩者; "check"=只偵測不動作。
    回傳結果 dict (含各元件相似度與 ok)。log 為訊息回呼。"""
    do_input = mode in ("input", "both", "check")
    do_button = mode in ("button", "both", "check")
    act = do_action and mode != "check"

    catalog = TemplateCatalog(template_root)
    try:
        device = catalog.normalize_device(device)
    except ValueError as exc:
        log(str(exc))
        return {"ok": False}
    win_g = _load_template(catalog, device, "window", log)
    if win_g is None:
        return {"ok": False}
    inp_g = _load_template(catalog, device, "input", log) if do_input else None
    btn_g = _load_template(catalog, device, "button", log) if do_button else None
    if do_input and inp_g is None:
        return {"ok": False}
    if do_button and btn_g is None:
        return {"ok": False}
    if kvm.frame is None:
        log("沒有影格 (確認 KVM 已連線且遠端有畫面)")
        return {"ok": False}

    frame = kvm.frame.copy()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    result = {"ok": False, "window": None, "input": None, "button": None}
    if hasattr(kvm, "rpc_errors"):
        kvm.rpc_errors.clear()      # 清掉舊錯誤, 之後檢查本次動作有無失敗

    # 1) 視窗 (一律先定位)
    wv, wl, wt, ww, wh = locate(gray, win_g, downscale=True)
    log(f"[視窗] 相似度 {wv:.2f}")
    result["window"] = wv
    if wl is None or wv < threshold:
        if annotate_path:
            cv2.imwrite(annotate_path, frame)
        log(f"找不到視窗 (相似度 {wv:.2f} < {threshold}), 建議重截 Pattern")
        return result
    # 視窗 ROI: 從視窗模板命中處頂端往下延伸到畫面底部、維持同寬。
    # 這樣 window 模板即使只截「上方工具列」這種穩定區塊, 也能涵蓋下方的
    # 輸入框/按鈕 (它們在視窗內、工具列下方)。origin = (wl, wt)。
    H = gray.shape[0]
    roi = gray[wt:H, wl:wl + ww]
    cv2.rectangle(frame, (wl, wt), (wl + ww, H - 1), (0, 255, 0), 2)

    ok = True

    # 2) 輸入框 (偏右點擊避開標籤)
    if do_input:
        iv, il, it, iw, ih = locate(roi, inp_g, downscale=False)
        result["input"] = iv
        input_pt = None
        if il is not None and iv >= threshold:
            ix, iy = wl + il, wt + it
            input_pt = (int(ix + iw * 0.75), int(iy + ih / 2))
            cv2.rectangle(frame, (ix, iy), (ix + iw, iy + ih), (255, 128, 0), 2)
            cv2.circle(frame, input_pt, 6, (0, 0, 255), -1)
        log(f"[輸入框] 相似度 {iv:.2f}  點擊點 {input_pt}")
        if input_pt is None:
            ok = False
        elif act:
            log(f"點擊輸入框並輸入 {sn_text}")
            await kvm.click(*input_pt)
            await kvm.type_text(sn_text)
            await kvm.press_enter()        # 輸入完按 Enter 確認
            log("按下 Enter")

    # 3) 按鈕 (在視窗 ROI 內找; OK 文字夠獨特, 門檻可擋掉弱誤判)
    if do_button:
        bv, bl, bt, bw, bh = locate(roi, btn_g, downscale=False)
        result["button"] = bv
        button_pt = None
        if bl is not None and bv >= threshold:
            bx, by = wl + bl, wt + bt
            button_pt = (int(bx + bw / 2), int(by + bh / 2))
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (0, 200, 255), 2)
            cv2.circle(frame, button_pt, 6, (0, 0, 255), -1)
        log(f"[按鈕] 相似度 {bv:.2f}  點擊點 {button_pt}")
        if button_pt is None:
            ok = False
        elif act:
            log("點擊按鈕")
            await kvm.click(*button_pt)

    if annotate_path:
        cv2.imwrite(annotate_path, frame)

    # 檢查裝置端有無回報錯誤 (例如 HID 寫入失敗: USB 鍵鼠連線中斷)
    if act:
        await asyncio.sleep(0.2)        # 等裝置的 error 回覆傳回
        errs = getattr(kvm, "rpc_errors", [])
        if errs:
            ok = False
            result["hid_error"] = errs[-1]
            log(f"裝置回報錯誤: {errs[-1]}")
            if "hidg" in errs[-1] or "endpoint" in errs[-1]:
                log("鍵鼠 USB 連線中斷! 請檢查 JetKVM 到遠端的 USB 線、喚醒遠端")

    result["ok"] = ok
    if not act:
        log("(只偵測, 未動作)")
    elif ok:
        log("流程完成")
    else:
        log("流程結束 (有錯誤或未完全命中)")
    return result
