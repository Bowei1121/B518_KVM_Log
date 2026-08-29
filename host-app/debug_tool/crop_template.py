# -*- coding: utf-8 -*-
"""
從 WebRTC 影格框選重截範本 -> 存到 FCT
==========================================
範本必須和「執行時的畫面來源」一致。現在改用 WebRTC 直接取畫面,
所以範本要從 WebRTC 影格 (jetkvm_frame.png) 重截。

步驟:
  1. 先跑一次 python jetkvm_auto.py (DRY_RUN), 它會存出乾淨影格 jetkvm_frame.png
  2. 重截三張範本:
       python crop_template.py target   # 框視窗(穩定區塊) -> FCT/target.png
       python crop_template.py input    # 只框輸入框        -> FCT/Input_target.png
       python crop_template.py button   # 只框 OK 按鈕      -> FCT/Button_target.png
     用滑鼠拖一個框 -> 放開即存檔; 按 Esc 取消。
  3. 重截後再跑 jetkvm_auto.py 確認相似度回到 0.9 以上。
"""
import os
import sys
import tkinter as tk

import cv2
from PIL import Image, ImageTk

TARGETS = {
    "target": "target.png",
    "input": "Input_target.png",
    "button": "Button_target.png",
}
SRC = "jetkvm_frame.png"   # 來源影格


def main():
    key = sys.argv[1].lower() if len(sys.argv) > 1 else "target"
    if key not in TARGETS:
        print(f"參數錯誤: {key!r}, 請用 target / input / button")
        return

    base = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(base, SRC)
    img = cv2.imread(src_path)
    if img is None:
        print(f"[X] 讀不到 {src_path}, 請先跑一次 jetkvm_auto.py 產生影格")
        return
    ih, iw = img.shape[:2]
    out_path = os.path.join(base, "FCT", TARGETS[key])
    os.makedirs(os.path.join(base, "FCT"), exist_ok=True)

    root = tk.Tk()
    root.title(f"框選 {key} -> {TARGETS[key]}  (拖曳框選, Esc 取消)")

    # 縮放以符合螢幕
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = min(1.0, sw * 0.9 / iw, sh * 0.85 / ih)
    disp_w, disp_h = int(iw * scale), int(ih * scale)

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb).resize((disp_w, disp_h))
    photo = ImageTk.PhotoImage(pil)

    canvas = tk.Canvas(root, width=disp_w, height=disp_h, cursor="cross")
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=photo)

    state = {"start": None, "rect": None}

    def on_down(e):
        state["start"] = (e.x, e.y)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="red", width=2)

    def on_move(e):
        if state["start"]:
            canvas.coords(state["rect"], state["start"][0], state["start"][1], e.x, e.y)

    def on_up(e):
        if not state["start"]:
            return
        x1, y1 = state["start"]
        x2, y2 = e.x, e.y
        state["start"] = None
        # 顯示座標 -> 原圖座標
        ix1, iy1 = int(min(x1, x2) / scale), int(min(y1, y2) / scale)
        ix2, iy2 = int(max(x1, x2) / scale), int(max(y1, y2) / scale)
        if ix2 - ix1 < 5 or iy2 - iy1 < 5:
            print("[!] 框太小, 取消")
            return
        crop = img[iy1:iy2, ix1:ix2]
        cv2.imwrite(out_path, crop)
        print(f"[OK] 已存 {out_path}  尺寸 {ix2-ix1}x{iy2-iy1}  位置 ({ix1},{iy1})")
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_move)
    canvas.bind("<ButtonRelease-1>", on_up)
    root.bind("<Escape>", lambda e: root.destroy())
    print(f"請在畫面上框選 {key} (拖曳, Esc 取消)...")
    root.mainloop()


if __name__ == "__main__":
    main()
