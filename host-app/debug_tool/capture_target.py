# -*- coding: utf-8 -*-
"""
範本框選截圖工具 (支援多螢幕) — 存檔到 FCT 資料夾

執行方式 (在目標所在的螢幕拖一個方框 -> 放開 -> 自動存檔, Esc 取消):
    python capture_target.py            # 截「視窗」 -> FCT/target.png
    python capture_target.py target     # 同上
    python capture_target.py input      # 截「輸入框」-> FCT/Input_target.png
    python capture_target.py button     # 截「按鈕」  -> FCT/Button_target.png

重點:
- DPI 感知與 ui_app.py 完全一致 (Per-Monitor Aware V2), 用 mss 擷取實體像素,
  確保截出來的範本和 ui_app 執行時的截圖「像素一致」, 才比對得到。
- 視窗(target): 框視窗裡「固定不變、有線條/文字對比」的區塊, 避開會變動的內容。
- 輸入框(input)/按鈕(button): 只框元件本身, 不要含周圍大片空白。
"""
import os
import sys
import tkinter as tk

# 與 ui_app.py 相同的 DPI 感知 (Per-Monitor Aware V2), 否則截圖比例會不一致
try:
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import mss
import numpy as np
import cv2

# 要截哪一張 -> 對應 FCT 內的檔名
TARGETS = {
    "target": "target.png",
    "input": "Input_target.png",
    "button": "Button_target.png",
}


def fct_path(filename):
    """專案根目錄 (debug_tool 的上一層) 下的 FCT 資料夾。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fct = os.path.join(root, "FCT")
    os.makedirs(fct, exist_ok=True)
    return os.path.join(fct, filename)


class Snipper:
    def __init__(self, out_path):
        self.out_path = out_path
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.v = v  # 虛擬桌面 (含 left/top 偏移)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.25)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.root.config(cursor="cross", bg="gray")

        self.canvas = tk.Canvas(self.root, bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.root.bind("<Escape>", lambda e: self.cancel())
        self.result = None

    def on_down(self, e):
        self.start = (e.x_root, e.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        cx, cy = e.x_root - self.v["left"], e.y_root - self.v["top"]
        self.rect = self.canvas.create_rectangle(cx, cy, cx, cy, outline="red", width=2)

    def on_move(self, e):
        if not self.start:
            return
        sx, sy = self.start[0] - self.v["left"], self.start[1] - self.v["top"]
        cx, cy = e.x_root - self.v["left"], e.y_root - self.v["top"]
        self.canvas.coords(self.rect, sx, sy, cx, cy)

    def on_up(self, e):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = e.x_root, e.y_root
        left, top = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        self.start = None
        if w < 5 or h < 5:
            print("[!] 框太小, 已取消")
            self.root.destroy()
            return
        self.result = {"left": left, "top": top, "width": w, "height": h}
        self.root.destroy()

    def cancel(self):
        print("[!] 已取消")
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        if not self.result:
            return
        with mss.mss() as sct:
            raw = sct.grab(self.result)
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        cv2.imwrite(self.out_path, img)
        r = self.result
        print(f"[OK] 已存檔: {self.out_path}  尺寸 {r['width']}x{r['height']}  "
              f"虛擬座標 left={r['left']} top={r['top']}")


if __name__ == "__main__":
    key = sys.argv[1].lower() if len(sys.argv) > 1 else "target"
    if key not in TARGETS:
        print(f"參數錯誤: {key!r}，請用 target / input / button")
        raise SystemExit(1)
    out = fct_path(TARGETS[key])
    print(f"準備截圖 -> {out}")
    print("請在『目標所在的螢幕』拖一個方框 (Esc 取消)...")
    Snipper(out).run()
