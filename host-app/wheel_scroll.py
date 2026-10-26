# -*- coding: utf-8 -*-
"""Mouse wheel for every scrollable area in the Monitor UI (issue #117). Windows first; macOS and Linux work too.

Windows sends the wheel to the widget that has keyboard focus, not the one under the pointer, so this module
looks the pointer up with ``winfo_containing``. Tk's own wheel bindings on Text and Listbox would scroll the
focused widget as well, so they are removed and every wheel event goes through here. Shift + wheel scrolls
sideways. A widget takes part once ``register`` has been called on it.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

WHEEL_NOTCH = 120  # Windows: one notch is 120; macOS sends small deltas, Linux sends Button-4 / Button-5
SHIFT_MASK = 0x0001
_FLAG = "_wheel_target"
_INSTALLED = "_wheel_installed"


def register(widget: Any, scrolls: Any = None) -> Any:
    """Route the wheel over ``widget`` to ``scrolls`` (defaults to the widget itself)."""
    setattr(widget, _FLAG, widget if scrolls is None else scrolls)
    return widget


def registered(widget: Any) -> bool:
    return bool(getattr(widget, _FLAG, False))


def units_for(delta: int) -> int:
    """Scroll units for one wheel event. Positive scrolls down, as ``yview_scroll`` expects.

    A Windows notch (±120) is one step per 120; a macOS event with a small delta still moves one step.
    """
    if delta == 0:
        return 0
    units = -int(delta / WHEEL_NOTCH)
    if units == 0:
        units = -1 if delta > 0 else 1
    return units


def target_under(root: tk.Misc, x_root: int, y_root: int) -> Any:
    """The nearest registered widget at or above the pointer, or None."""
    try:
        widget = root.winfo_containing(x_root, y_root)
    except (KeyError, tk.TclError):  # the pointer is over no widget of this application
        return None
    while widget is not None:
        if registered(widget):
            return getattr(widget, _FLAG)
        widget = widget.master
    return None


def _shift_held(event: Any) -> bool:
    state = event.state
    if isinstance(state, int):
        return bool(state & SHIFT_MASK)
    return "Shift" in str(state)


def _scroll(root: tk.Misc, event: Any, delta: int) -> str | None:
    target = target_under(root, event.x_root, event.y_root)
    if target is None:
        return None
    units = units_for(delta)
    if units:
        if _shift_held(event):
            target.xview_scroll(units, "units")
        else:
            target.yview_scroll(units, "units")
    return "break"


def install(root: tk.Misc) -> None:
    """Route every wheel event of ``root``'s application through this module. Calling it again does nothing.

    Any widget or window may be passed: the handler is bound once, on the application's root.
    """
    root = root._root()
    if getattr(root, _INSTALLED, False):
        return
    setattr(root, _INSTALLED, True)
    for widget_class in ("Text", "Listbox"):
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.tk.call("bind", widget_class, sequence, "")  # no class binding: it would scroll the focused widget
    root.bind_all("<MouseWheel>", lambda e: _scroll(root, e, int(e.delta)), add="+")
    root.bind_all("<Button-4>", lambda e: _scroll(root, e, WHEEL_NOTCH), add="+")
    root.bind_all("<Button-5>", lambda e: _scroll(root, e, -WHEEL_NOTCH), add="+")
