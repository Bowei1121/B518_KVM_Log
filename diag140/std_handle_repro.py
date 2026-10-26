"""Issue #140: does replacing fds 0/1/2 (what pytest's default --capture=fd does around every test)
make the next tk.Tk() fail, while Tk roots that cached the old std handles are still alive?"""
import json, os, sys, tkinter as tk
import msvcrt

K = int(os.environ.get("K", "60"))
mode = sys.argv[1]  # "churn" or "control"
saved = [os.dup(fd) for fd in (0, 1, 2)]
keep = tk.Tk(); keep.withdraw()  # like the module-level _probe roots: alive for the whole session
before = [msvcrt.get_osfhandle(fd) for fd in (0, 1, 2)]
fails, first, reused = 0, [], 0
for i in range(K):
    if mode == "churn":
        stale = [msvcrt.get_osfhandle(fd) for fd in (0, 1, 2)]
        for fd in (0, 1, 2):  # same as FDCapture start/done: dup2 a new file onto the fd, closing the old OS handle
            tmp = os.open(os.devnull, os.O_RDWR)
            os.dup2(tmp, fd)
            os.close(tmp)
    try:
        x = tk.Tk(); x.destroy(); del x
    except tk.TclError as e:
        fails += 1
        if len(first) < 3:
            first.append((i, str(e)[:300]))
for fd, s in zip((0, 1, 2), saved):
    os.dup2(s, fd)
print("REPRO140 " + json.dumps({"mode": mode, "K": K, "fails": fails, "first": first,
                                "tcl_std_handles_cached_at_start": before}))
