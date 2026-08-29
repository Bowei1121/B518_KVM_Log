# -*- coding: utf-8 -*-
"""
Pattern 工具 (模組化 crop_template 的框選裁切功能)
==================================================
PatternCropper: 在既有的 Tk 應用裡開一個 Toplevel, 讓使用者從一張影像
框選一塊區域, 存成 Pattern 檔。供 ui_app.py 的「創建Pattern」按鈕使用。
"""
import os
import tkinter as tk

import cv2
from PIL import Image, ImageTk


class PatternCropper(tk.Toplevel):
    def __init__(self, parent, src_path, out_path, on_done=None):
        """parent: 母視窗; src_path: 來源影像; out_path: 裁切後存檔路徑;
        on_done(success: bool, message: str): 完成/取消時的回呼 (可選)。"""
        super().__init__(parent)
        self.src_path = src_path
        self.out_path = out_path
        self.on_done = on_done
        self._notified = False

        img = cv2.imread(src_path)
        if img is None:
            self._finish(False, f"讀不到影像: {src_path}")
            self.destroy()
            return
        self.img = img
        ih, iw = img.shape[:2]

        self.title(f"框選 Pattern -> {os.path.basename(out_path)} (拖曳框選, Esc 取消)")

        # 縮放以符合螢幕
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = min(1.0, sw * 0.9 / iw, sh * 0.85 / ih)
        dw, dh = int(iw * self.scale), int(ih * self.scale)

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((dw, dh)))
        self.canvas = tk.Canvas(self, width=dw, height=dh, cursor="cross",
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        self._start = None
        self._rect = None
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.transient(parent)
        self.grab_set()        # 模態: 裁切時擋住主視窗

    def _finish(self, success, message):
        if not self._notified:
            self._notified = True
            if self.on_done:
                self.on_done(success, message)

    def _down(self, e):
        self._start = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                  outline="red", width=2)

    def _move(self, e):
        if self._start:
            self.canvas.coords(self._rect, self._start[0], self._start[1], e.x, e.y)

    def _up(self, e):
        if not self._start:
            return
        x1, y1 = self._start
        x2, y2 = e.x, e.y
        self._start = None
        ix1, iy1 = int(min(x1, x2) / self.scale), int(min(y1, y2) / self.scale)
        ix2, iy2 = int(max(x1, x2) / self.scale), int(max(y1, y2) / self.scale)
        if ix2 - ix1 < 5 or iy2 - iy1 < 5:
            self._finish(False, "框太小, 已取消")
            self.destroy()
            return
        crop = self.img[iy1:iy2, ix1:ix2]
        os.makedirs(os.path.dirname(os.path.abspath(self.out_path)), exist_ok=True)
        ok = cv2.imwrite(self.out_path, crop)
        if ok:
            self._finish(True, f"已存 Pattern: {self.out_path} ({ix2-ix1}x{iy2-iy1})")
        else:
            self._finish(False, f"存檔失敗: {self.out_path}")
        self.destroy()

    def _cancel(self):
        self._finish(False, "已取消創建 Pattern")
        self.destroy()
