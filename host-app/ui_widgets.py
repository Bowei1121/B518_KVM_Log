# -*- coding: utf-8 -*-
"""Shared look for the Monitor UI: fixed light palette, status lights and hover tooltips (issue #105).

Every Tk widget in the Monitor windows sets its bg/fg from the constants below, so macOS dark
mode cannot change them. ttk widgets follow the platform theme; macOS is pinned to light by
``NSRequiresAquaSystemAppearance`` in the packaging files.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Tuple, Union

# ---- Palette (light only) ---------------------------------------------------------
WINDOW_BG = "#f0f0f0"
PANEL_BG = "#ffffff"
TEXT_FG = "#222222"
MUTED_FG = "#555555"
INFO_FG = "#1565c0"
OK_FG = "#2e7d32"
WARN_FG = "#e65100"
ERROR_FG = "#c62828"
BUTTON_BG = "#e8e8e8"
ENTRY_BG = "#ffffff"
TIP_BG = "#fffde7"
PREVIEW_BG = "#1e1e1e"  # the live picture area stays dark so the frame reads clearly

LOG_BG = "#ffffff"
LOG_FG = TEXT_FG
LOG_ERROR_FG = ERROR_FG
LOG_WARN_FG = WARN_FG

# Status light colours
LED_GREEN = "#43a047"
LED_YELLOW = "#fdd835"
LED_RED = "#e53935"
LED_RED_DIM = "#ffcdd2"  # off-phase of a blinking red light

LED_COLORS = {"green": LED_GREEN, "yellow": LED_YELLOW, "red": LED_RED}

# Every colour a Monitor widget may use, for the palette test.
ALL_BG = frozenset({WINDOW_BG, PANEL_BG, BUTTON_BG, ENTRY_BG, TIP_BG, PREVIEW_BG, LOG_BG})
ALL_FG = frozenset({TEXT_FG, MUTED_FG, INFO_FG, OK_FG, WARN_FG, ERROR_FG, LOG_FG})


def apply_light_ttk_style(master: tk.Misc) -> None:
    """Pin ttk colours in the style (ttk has no per-widget bg) to the light palette."""
    style = ttk.Style(master)
    style.configure("TFrame", background=WINDOW_BG)
    style.configure("TLabel", background=WINDOW_BG, foreground=TEXT_FG)
    style.configure("TButton", background=BUTTON_BG, foreground=TEXT_FG)
    style.configure("TCheckbutton", background=WINDOW_BG, foreground=TEXT_FG)
    style.configure("TPanedwindow", background=WINDOW_BG)


def light_for_status(status: str) -> Tuple[str, bool]:
    """Map a Core station status to ``(colour key, blink)``. FROZEN blinks red; DISCONNECTED is red."""
    status_up = str(status or "").upper()
    if "BUSY" in status_up or "TESTING" in status_up:
        return "yellow", False
    if "FROZEN" in status_up:
        return "red", True
    if "IDLE" in status_up or "OK" in status_up:
        return "green", False
    return "red", False


class StatusLight(tk.Canvas):
    """One round lamp. ``set_light`` picks the colour; a blinking light toggles every 0.5 s."""

    BLINK_MS = 500

    def __init__(self, master: tk.Misc, diameter: int = 16, bg: str = WINDOW_BG) -> None:
        pad = 2
        size = diameter + 2 * pad
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0)
        self._color = LED_RED
        self._blink = False
        self._lit = True
        self._job: Optional[str] = None
        self._dot = self.create_oval(pad, pad, size - pad, size - pad, fill=LED_RED, outline="")

    def set_status(self, status: str) -> None:
        key, blink = light_for_status(status)
        self.set_light(key, blink)

    def set_light(self, key: str, blink: bool = False) -> None:
        self._color = LED_COLORS[key]
        self._blink = blink
        self._lit = True
        self.itemconfigure(self._dot, fill=self._color)
        if blink:
            self._schedule()
        else:
            self._cancel()

    def current_fill(self) -> str:
        return str(self.itemcget(self._dot, "fill"))

    def is_blinking(self) -> bool:
        return self._job is not None

    def _schedule(self) -> None:
        if self._job is None:
            self._job = self.after(self.BLINK_MS, self._tick)

    def _cancel(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _tick(self) -> None:
        self._job = None
        # A card rebuild destroys the lamp's parent without calling this lamp's destroy().
        if not self._blink or not self.winfo_exists():
            return
        self._lit = not self._lit
        self.itemconfigure(self._dot, fill=self._color if self._lit else LED_RED_DIM)
        self._schedule()

    def destroy(self) -> None:
        self._cancel()
        super().destroy()


class Tooltip:
    """Hover hint. ``text`` may be a callable so the hint always shows the latest value."""

    DELAY_MS = 400

    def __init__(self, widget: tk.Misc, text: Union[str, Callable[[], str]]) -> None:
        self.widget = widget
        self._text = text
        self._tip: Optional[tk.Toplevel] = None
        self._job: Optional[str] = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")

    def current_text(self) -> str:
        return self._text() if callable(self._text) else self._text

    def set_text(self, text: Union[str, Callable[[], str]]) -> None:
        self._text = text

    def is_visible(self) -> bool:
        return self._tip is not None

    def show(self) -> None:
        text = self.current_text()
        if self._tip is not None or not text:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tip, text=text, justify="left", bg=TIP_BG, fg=TEXT_FG,
            relief="solid", bd=1, padx=6, pady=3,
        ).pack()
        self._tip = tip

    def hide(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _on_enter(self, _event: object = None) -> None:
        if self._job is None:
            self._job = self.widget.after(self.DELAY_MS, self._show_now)

    def _show_now(self) -> None:
        self._job = None
        self.show()

    def _on_leave(self, _event: object = None) -> None:
        self.hide()
