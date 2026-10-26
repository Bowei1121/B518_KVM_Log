# -*- coding: utf-8 -*-
"""Shared look for the Monitor UI: fixed light palette, status lights and hover tooltips (issue #105).

Every Tk widget in the Monitor windows sets its bg/fg from the constants below, so macOS dark
mode cannot change them. ttk widgets follow the platform theme; macOS is pinned to light by
``NSRequiresAquaSystemAppearance`` in the packaging files.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Tuple, Union

from app_info import asset_path
from station_presentation import present_station
from ui_i18n import bind

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
LED_GREY = "#9e9e9e"
LED_RED_DIM = "#ffcdd2"  # off-phase of a blinking red light

LED_COLORS = {"green": LED_GREEN, "yellow": LED_YELLOW, "red": LED_RED, "grey": LED_GREY}

# Every colour a Monitor widget may use, for the palette test.
# Station row colours (spec v5.2 §14.1, issue #126); the table itself lives in station_presentation.
ROW_FG = frozenset({"#2E7D32", "#1565C0", "#616161", "#ffffff"})  # status words and colour bar
ROW_BG = frozenset({"#E8F5E9", "#E3F2FD", "#F5F5F5", "#B71C1C"})
LIST_TRACK_BG = "#ECEFF1"  # station list scroll track (spec v5.3 §14.10)
ALL_BG = (frozenset({WINDOW_BG, PANEL_BG, BUTTON_BG, ENTRY_BG, TIP_BG, PREVIEW_BG, LOG_BG, LIST_TRACK_BG})
          | ROW_BG | ROW_FG)
# Station list hint line: red when an off-screen device is abnormal, else grey (spec v5.3 §14.10).
LIST_HINT_FG = frozenset({"#B71C1C", "#546E7A"})
ALL_FG = frozenset({TEXT_FG, MUTED_FG, INFO_FG, OK_FG, WARN_FG, ERROR_FG, LOG_FG}) | ROW_FG | LIST_HINT_FG


def apply_light_ttk_style(master: tk.Misc) -> None:
    """Pin ttk colours in the style (ttk has no per-widget bg) to the light palette."""
    style = ttk.Style(master)
    style.configure("TFrame", background=WINDOW_BG)
    style.configure("TLabel", background=WINDOW_BG, foreground=TEXT_FG)
    style.configure("TButton", background=BUTTON_BG, foreground=TEXT_FG)
    style.configure("TCheckbutton", background=WINDOW_BG, foreground=TEXT_FG)
    style.configure("TPanedwindow", background=WINDOW_BG)
    style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=TEXT_FG)
    style.configure("TCombobox", fieldbackground=ENTRY_BG, background=BUTTON_BG, foreground=TEXT_FG)
    style.configure("TScrollbar", background=WINDOW_BG, troughcolor=WINDOW_BG)


def _set_aqua(window: tk.Misc) -> None:
    try:
        window.tk.call("::tk::unsupported::MacWindowStyle", "appearance", str(window), "aqua")
    except tk.TclError:
        pass  # Tk without appearance control never goes dark; a destroyed window has nothing to pin


def pin_light_appearance(window: tk.Misc) -> None:
    """macOS: keep this window in the light (aqua) appearance even when the system is in Dark Mode.

    Tk ignores the request until the window is mapped, so it is also re-applied on <Map>. The binding
    dies with the window (an after_idle callback can outlive it and crash Tk). Call it on every Toplevel,
    not only the root. No-op on other platforms.
    """
    if sys.platform != "darwin":
        return
    if isinstance(window, tk.Tk):
        # Root: call once the caller has realised it. A <Map> handler on the root segfaults Tk 9 at startup.
        _set_aqua(window)
        return
    # Toplevel: the appearance can only be set once it is mapped, so pin on every map.
    window.bind("<Map>", lambda _event: _set_aqua(window), add="+")


# ---- Window icon (issue #115) ----------------------------------------------------------
ICON_SIZES = (16, 32, 48)
_ICON_ATTR = "_atlas_icon_images"  # kept on the Tk root: Tk drops an image that has no Python reference


def apply_window_icon(window: tk.Misc) -> None:
    """Give ``window`` the bridge icon (``host-app/assets/bridge_<size>.png``). Child windows use the root's images.

    A missing or unreadable asset leaves Tk's default icon in place; it never stops the window from opening.
    """
    root = window._root()
    images = getattr(root, _ICON_ATTR, None)
    if images is None:
        try:
            images = [tk.PhotoImage(master=root, file=str(asset_path(f"bridge_{size}.png"))) for size in ICON_SIZES]
        except (tk.TclError, OSError):
            return
        setattr(root, _ICON_ATTR, images)
    try:
        window.iconphoto(False, *images)
    except tk.TclError:
        pass


def measure_all_languages(window: tk.Misc, languages: Tuple[str, ...], get_language: Callable[[], str],
                          set_language: Callable[[str], None]) -> Tuple[int, int]:
    """Largest requested ``(width, height)`` of ``window`` over every language, so a switch never truncates.

    The language is put back afterwards. The window's own geometry is not changed.
    """
    original = get_language()
    width = height = 0
    try:
        for lang in languages:
            set_language(lang)
            window.update_idletasks()
            width = max(width, window.winfo_reqwidth())
            height = max(height, window.winfo_reqheight())
    finally:
        set_language(original)
    window.update_idletasks()
    return width, height


FIXED_WIDTH_MARGIN = 1.25  # widths are fixed with this much room over the widest measured text (#117)


def iter_widgets(widget: tk.Misc):
    """``widget`` and every widget below it, parents first."""
    yield widget
    for child in widget.winfo_children():
        yield from iter_widgets(child)


def _width_is_free(widget: tk.Misc) -> bool:
    """A text Label or Button whose width nothing has fixed yet. Images and text variables keep their own size."""
    if widget.winfo_class() not in ("Label", "Button", "TLabel", "TButton"):  # tk and ttk (PatternCropper is ttk)
        return False
    if int(str(widget.cget("width")) or 0) != 0:
        return False
    return not (str(widget.cget("image")) or str(widget.cget("textvariable")))


def _font_of(widget: tk.Misc):
    """The font a tk or ttk widget draws its text with. ttk widgets take it from their style, not an option."""
    font = ""
    try:
        font = str(widget.cget("font"))  # ttk.Label has the option, but it is empty unless the style sets it
    except tk.TclError:  # ttk.Button has no font option at all
        pass
    if font:
        return font
    if not widget.winfo_class().startswith("T"):  # a tk widget with no font: the default one
        return "TkDefaultFont"
    style = str(widget.cget("style") or widget.winfo_class())
    return str(widget.tk.call("ttk::style", "lookup", style, "-font")) or "TkDefaultFont"


def fix_text_widths(window: tk.Misc, languages: Tuple[str, ...], get_language: Callable[[], str],
                    set_language: Callable[[str], None]) -> None:
    """Give every free text Label and Button the width of its longest text over ``languages`` (issue #117).

    After this, a language switch changes the words but never a widget's size or position. The width is stored in
    characters, and ``wraplength`` wraps a message that is longer than that (for example one that appears later),
    so nothing is cut. Widgets already given a width are left alone, so calling this again is harmless.
    """
    widgets = [w for w in iter_widgets(window) if _width_is_free(w)]
    if not widgets:
        return
    longest = {id(w): 0 for w in widgets}
    original = get_language()
    try:
        for lang in languages:
            set_language(lang)
            window.update_idletasks()
            for w in widgets:
                try:
                    longest[id(w)] = max(longest[id(w)], w.winfo_reqwidth())
                except tk.TclError:  # destroyed by this language's own re-render
                    pass
    finally:
        set_language(original)
    for w in widgets:
        if longest[id(w)] == 0:  # no text yet (an error line filled in later): nothing to keep still
            continue
        try:
            zero = int(w.tk.call("font", "measure", _font_of(w), "0")) or 1  # Tk counts width in "0" widths
            # Windows draws some text wider than Tk measured it (fallback glyphs such as ✂); without a margin the
            # text wraps there, the row grows and the window cut it (CI, #117). The margin keeps one line.
            chars = max(1, -(-int(longest[id(w)] * FIXED_WIDTH_MARGIN) // zero))
            if w.winfo_class() in ("Label", "TLabel"):
                # A Label shows run-time text too (a check result): wrap it rather than cut it (review R2-1)
                w.configure(width=chars, wraplength=chars * zero)
            else:
                w.configure(width=chars)  # a Button has fixed text: no wrap, so its row keeps its height
        except tk.TclError:
            continue
    window.update_idletasks()


def fit_label_to_pixels(label: tk.Misc, px: int, wrap: bool = True) -> None:
    """Give a Label a fixed width of at most ``px`` pixels, whatever unit the platform counts its ``width`` in.

    Tk counts ``width`` in characters, but the pixels per character differ from a "0" on Windows (CI, #117 R3-2c). So
    the width is set from a guess and then corrected from the width Tk really asks for. ``wrap`` also wraps at it.
    """
    zero = int(label.tk.call("font", "measure", _font_of(label), "0")) or 1
    label.configure(wraplength=0)
    chars = max(1, px // zero)
    for _ in range(3):
        label.configure(width=chars)
        label.update_idletasks()
        asked = label.winfo_reqwidth()
        if asked <= px or chars == 1:
            break
        chars = max(1, int(chars * px / asked))
    inner = max(1, label.winfo_reqwidth() - 2 * (label.winfo_pixels(label.cget("padx")) + label.winfo_pixels(label.cget("bd"))
                                                  + label.winfo_pixels(label.cget("highlightthickness"))))
    if wrap:
        label.configure(wraplength=inner)


def reserve_text_width(widget: tk.Misc, chars: int, lines: int = 1) -> None:
    """Give a Label that shows run-time text a fixed box of ``chars`` x ``lines`` (#117 review R2-1, R3-1).

    The box never changes with the text or the language, so a long result cannot push other widgets around. A text
    that needs more lines is cut with "…" by ``bind_clamped``; the whole text is in the Log and the hover hint.
    """
    font = _font_of(widget)
    zero = int(widget.tk.call("font", "measure", font, "0")) or 1
    widget.configure(height=lines)
    fit_label_to_pixels(widget, chars * zero)
    widget._clamp_lines = lines  # type: ignore[attr-defined]
    widget._full_text = ""  # type: ignore[attr-defined]


def clamp_text(widget: tk.Misc, text: str, lines: int) -> str:
    """``text`` shortened with "…" so that it wraps into at most ``lines`` lines of the widget's wrap width.

    The wrap is simulated with Tk's own font measure at 95% of the width, so the real wrap (Windows draws some text
    wider than Tk measures it) never needs more lines than this counts.
    """
    wrap = int(str(widget.cget("wraplength")) or 0)
    if wrap <= 0:
        return text
    font = _font_of(widget)
    limit = wrap * 0.95

    def measure(chunk: str) -> int:
        return int(widget.tk.call("font", "measure", font, chunk))

    rows: List[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else current + " " + word
            if measure(candidate) <= limit:
                current = candidate
                continue
            if current:
                rows.append(current)
                current = ""
            while word and measure(word) > limit:  # a word longer than a row (a path, or Chinese text) breaks anywhere
                cut = len(word)
                while cut > 1 and measure(word[:cut]) > limit:
                    cut -= 1
                rows.append(word[:cut])
                word = word[cut:]
            current = word
        rows.append(current)
    if len(rows) <= lines:
        return text
    last = rows[lines - 1]
    while last and measure(last + "…") > limit:
        last = last[:-1]
    return "\n".join(rows[:lines - 1] + [last.rstrip() + "…"])


def bind_clamped(widget: tk.Misc, render: Callable[[], str]) -> None:
    """``bind`` for a label made by ``reserve_text_width``: shows ``render()`` cut to the label's lines.

    The whole text is kept for the hover hint (``full_text``) and the cut is redone on every language switch, so the
    box stays the same size in either language.
    """
    lines = getattr(widget, "_clamp_lines", 0)
    if not lines:
        bind(widget, render)
        return

    def shown() -> str:
        full = render()
        text = clamp_text(widget, full, lines)
        widget._full_text = full if text != full else ""  # type: ignore[attr-defined]
        return text

    bind(widget, shown)


def full_text(widget: tk.Misc) -> str:
    """The whole text of a cut label ("" when nothing was cut), for its hover hint."""
    return getattr(widget, "_full_text", "")


def fix_width_to_texts(widget: tk.Misc, texts) -> None:
    """Fix one text Label/Button to the widest of ``texts`` (one per language), without switching languages.

    For widgets created while the window is already open (a settings row): switching the whole UI for each new
    row was the slow part (#117 review). Same margin and rule as ``fix_text_widths``.
    """
    if not _width_is_free(widget):
        return
    font = _font_of(widget)
    px = max(int(widget.tk.call("font", "measure", font, text)) for text in texts)
    zero = int(widget.tk.call("font", "measure", font, "0")) or 1
    chars = max(1, -(-int((px + 24) * FIXED_WIDTH_MARGIN) // zero))  # +24: the button's own padding and border
    widget.configure(width=chars)


def is_dark_appearance(window: tk.Misc) -> bool:
    """macOS: True when Tk reports the window as dark. Always False elsewhere."""
    if sys.platform != "darwin":
        return False
    return bool(int(window.tk.call("::tk::unsupported::MacWindowStyle", "isdark", str(window))))


def light_for_status(status: str) -> Tuple[str, bool]:
    """Map a Core station status to ``(colour key, blink)``. FROZEN is solid red; DISCONNECTED is grey."""
    pres = present_station({"status": status})
    return pres.light, pres.blink


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
        color = LED_COLORS[key]
        # The poll repeats the same status every 1.5 s. Keep the current blink phase instead of resetting it.
        if color == self._color and blink == self._blink and (not blink or self._job is not None):
            return
        self._color = color
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

    DELAY_MS = 500  # about half a second, as the #106 spec asks

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
        pin_light_appearance(tip)
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
