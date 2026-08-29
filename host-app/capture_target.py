# -*- coding: utf-8 -*-
"""
target.png 框選截圖工具 (支援多螢幕)

執行:  python capture_target.py
操作:  在「目標所在的螢幕」用滑鼠拖一個方框 -> 放開 -> 自動存成 target.png
       按 Esc 取消。

重點:
- 用 mss 擷取 (實體像素), 與 ui_app.py 的 on_test 完全一致, 確保比對得到。
- 建議只框「小而有特徵、不會變動」的區域 (例如標題列圖示+檔名),
  不要框整個視窗, 也避開會變的內文文字。
"""
import os
import sys
import tkinter as tk

try:
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import mss
import numpy as np
import cv2


def save_dir():
    return os.path.dirname(os.path.abspath(__file__))


class Snipper:
    def __init__(self):
        with mss.mss() as sct:
            v = sct.monitors[0]
        self.v = v  # 虛擬桌面 (含 left/top 偏移)

        self.root = tk.Tk()
        self.root.overrideredirect(True)               # 無邊框
        self.root.attributes("-alpha", 0.25)           # 半透明, 看得到底下畫面
        self.root.attributes("-topmost", True)
        # 視窗鋪滿整個虛擬桌面 (可為負座標)
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
        # 以 canvas 內座標畫框 (canvas 0,0 對應虛擬桌面 left,top)
        cx, cy = e.x_root - self.v["left"], e.y_root - self.v["top"]
        self.rect = self.canvas.create_rectangle(cx, cy, cx, cy,
                                                 outline="red", width=2)

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
        # 隱藏視窗後再截圖 (此處視窗已 destroy)
        with mss.mss() as sct:
            raw = sct.grab(self.result)
            img = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        out = os.path.join(save_dir(), "target.png")
        cv2.imwrite(out, img)
        r = self.result
        print(f"[OK] 已存檔: {out}  尺寸 {r['width']}x{r['height']}  "
              f"虛擬座標 left={r['left']} top={r['top']}")


if __name__ == "__main__":
    print("請在『目標所在的螢幕』拖一個方框 (Esc 取消)...")
    Snipper().run()
