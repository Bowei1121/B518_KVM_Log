# -*- coding: utf-8 -*-
"""
SN 文字辨識 (OCR) — 使用 RapidOCR (onnxruntime, 免外部安裝)
============================================================
read_sn(bgr_crop): 對裁切影像做 OCR, 回傳最像 SN 的字串 (最長的英數 token)。
引擎延遲初始化 (第一次呼叫才載入模型, 約 1~2 秒)。缺套件/失敗時回 ''。
"""
import re

_engine = None
_init_failed = False


def _get_engine():
    global _engine, _init_failed
    if _engine is None and not _init_failed:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        except Exception:
            _init_failed = True
            _engine = None
    return _engine


def warmup():
    """預先載入 OCR 模型 (可在背景執行緒呼叫, 讓第一次 check 不卡)。"""
    eng = _get_engine()
    if eng is not None:
        try:
            import numpy as np
            eng(np.zeros((32, 64, 3), dtype=np.uint8))
        except Exception:
            pass


def read_lines(bgr):
    """對整張影像做一次 OCR, 回傳 [(sn_text, y_center), ...]
    (text 去掉非英數字元, y_center 為該行在影像中的垂直中心)。
    一次讀多列可避免每列各呼叫一次 OCR 的耗時。"""
    if bgr is None or bgr.size == 0:
        return []
    eng = _get_engine()
    if eng is None:
        return []
    try:
        result, _ = eng(bgr)
    except Exception:
        return []
    out = []
    for item in (result or []):
        box, text = item[0], item[1]
        yc = sum(float(p[1]) for p in box) / len(box)
        clean = re.sub(r"[^A-Za-z0-9]", "", str(text))
        if clean:
            out.append((clean, yc))
    return out


def read_sn(bgr_crop):
    """對 bgr_crop (BGR numpy, 應只含 SN 那一格) 做 OCR, 回傳 SN 字串; 失敗回 ''。
    SN 欄整格就是序號, 故把所有偵測文字接起來、去掉非英數字元 (OCR 有時會
    在中間插空格, 例如 'HK5...00003 YV', 需併回成 'HK5...00003YV')。"""
    if bgr_crop is None or bgr_crop.size == 0:
        return ""
    eng = _get_engine()
    if eng is None:
        return ""
    try:
        result, _ = eng(bgr_crop)
    except Exception:
        return ""
    if not result:
        return ""
    joined = "".join(str(item[1]) for item in result if len(item) > 1)
    return re.sub(r"[^A-Za-z0-9]", "", joined)
