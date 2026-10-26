"""Issue #140, mechanism check: Tcl cached the std handles when the first Tk root was made. If one of those
HANDLE values is closed and then handed out again for Tcl's next file open, does tk.Tk() fail?"""
import json, os, sys, tkinter as tk, msvcrt
fd = int(sys.argv[1])  # 0, 1 or 2
keep = tk.Tk(); keep.withdraw()          # holds Tcl's std channels (like the module-level _probe roots)
saved = os.dup(fd)
stale = msvcrt.get_osfhandle(fd)
results = []
for i in range(20):
    os.close(fd)                          # CloseHandle(stale): value free for reuse, Tcl still caches it
    try:
        x = tk.Tk(); x.destroy(); del x
        results.append("ok")
    except tk.TclError as e:
        results.append(str(e).splitlines()[0][:70] + " | " + " / ".join(str(e).splitlines()[2:3])[:160])
    os.dup2(saved, fd)                    # put a fresh handle back on the fd
    stale = msvcrt.get_osfhandle(fd)
print("STALE140 " + json.dumps({"fd": fd, "fails": sum(r != "ok" for r in results), "samples": sorted(set(results))[:4]}))
