# -*- coding: utf-8 -*-
"""Interactive, catalog-driven JetKVM template cropper."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk


class PatternCropper(tk.Toplevel):
    """Create one canonical device template without exposing file naming controls."""

    def __init__(self, parent, catalog, src_path=None, device="FCT", template_key="window", on_saved=None):
        super().__init__(parent)
        self.catalog, self.on_saved = catalog, on_saved
        self.img = self.photo = None
        self.scale = 1.0
        self._start = self._rect = self._selection = None
        self.title("製作 JetKVM 模板")
        self._build_controls(device, template_key)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.transient(parent)
        self.grab_set()
        if src_path:
            self.load_image(src_path)

    def _build_controls(self, device, template_key):
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")
        left = ttk.Frame(toolbar)
        left.pack(side="left")
        ttk.Button(left, text="選擇截圖", command=self._choose_image).pack(side="left", padx=(0, 6))
        ttk.Button(left, text="使用最新擷取", command=self._load_latest_capture).pack(side="left")
        right = ttk.Frame(toolbar)
        right.pack(side="right")
        ttk.Label(right, text="設備").pack(side="left", padx=(0, 4))
        self.device_var = tk.StringVar(value=self.catalog.normalize_device(device))
        self.device_box = ttk.Combobox(right, textvariable=self.device_var, values=self.catalog.devices(),
                                       state="readonly", width=7)
        self.device_box.pack(side="left", padx=(0, 10))
        self.device_box.bind("<<ComboboxSelected>>", self._on_device_changed)
        ttk.Label(right, text="模板種類").pack(side="left", padx=(0, 4))
        self.key_var = tk.StringVar()
        self.key_box = ttk.Combobox(right, textvariable=self.key_var, state="readonly", width=20)
        self.key_box.pack(side="left")
        self.key_box.bind("<<ComboboxSelected>>", self._update_target)
        self._refresh_keys(template_key)

        output = ttk.Frame(self, padding=(8, 0, 8, 6))
        output.pack(fill="x")
        ttk.Label(output, text="輸出檔案").pack(side="left", padx=(0, 6))
        self.target_var = tk.StringVar()
        ttk.Entry(output, textvariable=self.target_var, state="readonly").pack(side="left", fill="x", expand=True)
        self._update_target()
        self.canvas_holder = ttk.Frame(self, padding=(8, 0, 8, 4))
        self.canvas_holder.pack(fill="both", expand=True)
        self.hint_var = tk.StringVar(value="選擇截圖後，以滑鼠拖曳框選模板區域。")
        ttk.Label(self, textvariable=self.hint_var, padding=(8, 2)).pack(anchor="w")
        footer = ttk.Frame(self, padding=8)
        footer.pack(fill="x")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="儲存模板", command=self._save).pack(side="right", padx=(0, 6))

    def _refresh_keys(self, preferred_key=None):
        options = self.catalog.selection_options(self.device_var.get())
        self._key_by_label = dict(options)
        self.key_box["values"] = tuple(label for label, _key in options)
        selected = next((label for label, key in options if key == preferred_key), options[0][0])
        self.key_var.set(selected)

    def _selected_key(self):
        return self._key_by_label[self.key_var.get()]

    def _on_device_changed(self, _event=None):
        self._refresh_keys()
        self._update_target()

    def _update_target(self, _event=None):
        self.target_var.set(str(self.catalog.path(self.device_var.get(), self._selected_key())))

    def _choose_image(self):
        path = filedialog.askopenfilename(parent=self, title="選擇 JetKVM 截圖",
                                          filetypes=(("圖片檔", "*.png *.jpg *.jpeg"), ("所有檔案", "*.*")))
        if path:
            self.load_image(path)

    def _load_latest_capture(self):
        self.load_image(str(self.catalog.capture_path()))

    def load_image(self, path):
        image = cv2.imread(path)
        if image is None:
            messagebox.showerror("無法載入圖片", "讀不到影像: {}".format(path), parent=self)
            return
        self.img, self._selection, self._start, self._rect = image, None, None, None
        self._render_canvas()
        self.hint_var.set("已載入 {}；拖曳框選模板區域。".format(Path(path).name))

    def _render_canvas(self):
        for child in self.canvas_holder.winfo_children():
            child.destroy()
        ih, iw = self.img.shape[:2]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = min(1.0, sw * 0.88 / iw, (sh - 200) * 0.82 / ih)
        dw, dh = max(1, int(iw * self.scale)), max(1, int(ih * self.scale))
        rgb = cv2.cvtColor(self.img, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((dw, dh)))
        self.canvas = tk.Canvas(self.canvas_holder, width=dw, height=dh, cursor="cross", highlightthickness=0)
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)

    def _down(self, event):
        self._start, self._selection = (event.x, event.y), None
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="red", width=2)

    def _move(self, event):
        if self._start and self._rect:
            self.canvas.coords(self._rect, self._start[0], self._start[1], event.x, event.y)

    def _up(self, event):
        if not self._start:
            return
        x1, y1 = self._start
        self._start = None
        ix1, iy1 = int(min(x1, event.x) / self.scale), int(min(y1, event.y) / self.scale)
        ix2, iy2 = int(max(x1, event.x) / self.scale), int(max(y1, event.y) / self.scale)
        self._selection = (ix1, iy1, ix2, iy2)
        self.hint_var.set("已框選 {} x {} 像素；按「儲存模板」寫入指定檔案。".format(ix2 - ix1, iy2 - iy1))

    def _save(self):
        if self.img is None:
            messagebox.showwarning("尚未載入圖片", "請先選擇截圖或使用最新擷取。", parent=self)
            return
        if self._selection is None:
            messagebox.showwarning("尚未框選", "請以滑鼠拖曳框選模板區域。", parent=self)
            return
        device, key = self.device_var.get(), self._selected_key()
        target = self.catalog.path(device, key)
        overwrite = False
        if target.exists():
            overwrite = messagebox.askyesno("確認覆寫模板", "目標檔案已存在，是否覆寫？\n\n{}".format(target),
                                             parent=self)
            if not overwrite:
                self.hint_var.set("已取消覆寫，原模板未變更。")
                return
        try:
            saved = self.catalog.save_crop(self.img, self._selection, device, key, overwrite=overwrite)
        except (ValueError, FileExistsError, OSError) as exc:
            messagebox.showerror("儲存失敗", str(exc), parent=self)
            return
        self._selection = None
        if self._rect:
            self.canvas.delete(self._rect)
            self._rect = None
        message = "已儲存模板: {}".format(saved)
        self.hint_var.set(message + "；可繼續選擇其他種類製作。")
        messagebox.showinfo("模板已儲存", message, parent=self)
        if self.on_saved:
            self.on_saved(message)
