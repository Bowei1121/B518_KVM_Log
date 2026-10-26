"""pytest plugin: at every tk.Tk(), are the std handles Tcl cached at the first Tk() still the live ones?"""
import tkinter, msvcrt, collections
state = {"cached": None, "made": 0, "stale_made": 0, "fail_stale": 0, "fail_fresh": 0}
_orig = tkinter.Tk.__init__
def _init(self, *a, **k):
    cur = tuple(msvcrt.get_osfhandle(fd) for fd in (0, 1, 2))
    if state["cached"] is None:
        state["cached"] = cur
    stale = cur != state["cached"]
    state["made"] += 1; state["stale_made"] += stale
    try:
        _orig(self, *a, **k)
    except tkinter.TclError:
        state["fail_stale" if stale else "fail_fresh"] += 1
        raise
tkinter.Tk.__init__ = _init
def pytest_unconfigure(config):
    import os
    os.write(2, ("STDWATCH140 %r\n" % state).encode())
