# -*- coding: utf-8 -*-
"""Interactive, catalog-driven JetKVM template cropper."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

import cv2
from PIL import Image, ImageTk

import ui_i18n
from ui_i18n import bind, t
from ui_widgets import (WINDOW_BG, Tooltip, apply_light_ttk_style, apply_window_icon, fix_text_widths,
                        pin_light_appearance)


class PatternCropper(tk.Toplevel):
    """Create one canonical device template without exposing file naming controls."""

    def __init__(self, parent, catalog, src_path=None, device="FCT", template_key="window", on_saved=None):
        super().__init__(parent)
        self.catalog, self.on_saved = catalog, on_saved
        self.img = self.photo = None
        self.scale = 1.0
        self._start = self._rect = self._selection = None
        bind(self, "pt_title", option="title")
        self.configure(bg=WINDOW_BG)
        pin_light_appearance(self)
        apply_window_icon(self)
        apply_light_ttk_style(self)
        self._build_controls(device, template_key)
        fix_text_widths(self, ui_i18n.LANGUAGES, ui_i18n.current_language, ui_i18n.set_language)  # no resize on 🌐 (#117)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Destroy>", self._on_destroy, add="+")
        ui_i18n.on_language_change(self._relabel)
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
        choose = bind(ttk.Button(left, command=self._choose_image), "pt_choose")
        choose.pack(side="left", padx=(0, 6))
        Tooltip(choose, lambda: t("tip_pt_choose"))
        latest = bind(ttk.Button(left, command=self._load_latest_capture), "pt_latest")
        latest.pack(side="left")
        Tooltip(latest, lambda: t("tip_pt_latest"))
        right = ttk.Frame(toolbar)
        right.pack(side="right")
        bind(ttk.Label(right), "pt_device").pack(side="left", padx=(0, 4))
        self.device_var = tk.StringVar(value=self.catalog.normalize_device(device))
        self.device_box = ttk.Combobox(right, textvariable=self.device_var, values=self.catalog.devices(),
                                       state="readonly", width=7)
        self.device_box.pack(side="left", padx=(0, 10))
        self.device_box.bind("<<ComboboxSelected>>", self._on_device_changed)
        Tooltip(self.device_box, lambda: t("tip_pt_device"))
        bind(ttk.Label(right), "pt_type").pack(side="left", padx=(0, 4))
        self.key_var = tk.StringVar()
        self.key_box = ttk.Combobox(right, textvariable=self.key_var, state="readonly", width=20)
        self.key_box.pack(side="left")
        self.key_box.bind("<<ComboboxSelected>>", self._update_target)
        Tooltip(self.key_box, lambda: t("tip_pt_type"))
        self._refresh_keys(template_key)

        output = ttk.Frame(self, padding=(8, 0, 8, 6))
        output.pack(fill="x")
        bind(ttk.Label(output), "pt_output").pack(side="left", padx=(0, 6))
        self.target_var = tk.StringVar()
        target_entry = ttk.Entry(output, textvariable=self.target_var, state="readonly")
        target_entry.pack(side="left", fill="x", expand=True)
        Tooltip(target_entry, lambda: t("tip_pt_output"))
        self._update_target()
        self.canvas_holder = ttk.Frame(self, padding=(8, 0, 8, 4))
        self.canvas_holder.pack(fill="both", expand=True)
        self._hint: Callable[[], str] = lambda: t("pt_hint_start")
        self.hint_var = tk.StringVar(value=self._hint())
        ttk.Label(self, textvariable=self.hint_var, padding=(8, 2)).pack(anchor="w")
        footer = ttk.Frame(self, padding=8)
        footer.pack(fill="x")
        cancel = bind(ttk.Button(footer, command=self.destroy), "pt_cancel")
        cancel.pack(side="right")
        Tooltip(cancel, lambda: t("tip_pt_cancel"))
        save = bind(ttk.Button(footer, command=self._save), "pt_save")
        save.pack(side="right", padx=(0, 6))
        Tooltip(save, lambda: t("tip_pt_save"))

    def _set_hint(self, render: Callable[[], str]) -> None:
        """Status line under the picture; kept as a render so a language switch re-words it."""
        self._hint = render
        self.hint_var.set(render())

    def _relabel(self) -> None:
        if self.winfo_exists():
            self.hint_var.set(self._hint())

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            ui_i18n.off_language_change(self._relabel)

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
        path = filedialog.askopenfilename(parent=self, title=t("pt_dialog_title"),
                                          filetypes=((t("pt_file_images"), "*.png *.jpg *.jpeg"),
                                                     (t("pt_file_all"), "*.*")))
        if path:
            self.load_image(path)

    def _load_latest_capture(self):
        self.load_image(str(self.catalog.capture_path()))

    def load_image(self, path):
        image = cv2.imread(path)
        if image is None:
            messagebox.showerror(t("pt_load_title"), t("pt_load_fail", path=path), parent=self)
            return
        self.img, self._selection, self._start, self._rect = image, None, None, None
        self._render_canvas()
        name = Path(path).name
        self._set_hint(lambda: t("pt_loaded", name=name))

    def _render_canvas(self):
        for child in self.canvas_holder.winfo_children():
            child.destroy()
        ih, iw = self.img.shape[:2]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = min(1.0, sw * 0.88 / iw, (sh - 200) * 0.82 / ih)
        dw, dh = max(1, int(iw * self.scale)), max(1, int(ih * self.scale))
        rgb = cv2.cvtColor(self.img, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb).resize((dw, dh)))
        self.canvas = tk.Canvas(self.canvas_holder, width=dw, height=dh, cursor="cross", highlightthickness=0,
                                bg=WINDOW_BG)
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
        width, height = ix2 - ix1, iy2 - iy1
        self._set_hint(lambda: t("pt_selected", w=width, h=height))

    def _save(self):
        if self.img is None:
            messagebox.showwarning(t("pt_no_image_title"), t("pt_no_image_msg"), parent=self)
            return
        if self._selection is None:
            messagebox.showwarning(t("pt_no_selection_title"), t("pt_no_selection_msg"), parent=self)
            return
        device, key = self.device_var.get(), self._selected_key()
        target = self.catalog.path(device, key)
        overwrite = False
        if target.exists():
            overwrite = messagebox.askyesno(t("pt_overwrite_title"), t("pt_overwrite_msg", path=target),
                                            parent=self)
            if not overwrite:
                self._set_hint(lambda: t("pt_overwrite_cancel"))
                return
        try:
            saved = self.catalog.save_crop(self.img, self._selection, device, key, overwrite=overwrite)
        except (ValueError, FileExistsError, OSError) as exc:
            messagebox.showerror(t("pt_save_title"), str(exc), parent=self)
            return
        self._selection = None
        if self._rect:
            self.canvas.delete(self._rect)
            self._rect = None
        message = t("pt_saved", path=saved)
        self._set_hint(lambda: t("pt_saved", path=saved) + t("pt_more"))
        messagebox.showinfo(t("pt_saved_title"), message, parent=self)
        if self.on_saved:
            self.on_saved(str(saved))
