"""Issue #140 diagnostics: how often does Tcl/Tk fail to read its own library files on this runner?"""
import os, sys, time, threading, socket, collections, json
import tkinter as tk

label = sys.argv[1] if len(sys.argv) > 1 else "run"
N_TK = int(os.environ.get("N_TK", "300"))
N_READ = int(os.environ.get("N_READ", "2000"))
out = {"label": label}

# A. Tcl-level open+read of the library files, in one interpreter (no interpreter creation involved).
r = tk.Tk(); r.withdraw()
t0 = time.time()
res = r.tk.eval(r'''
set fails {}
set n 0
for {set i 0} {$i < %d} {incr i} {
  foreach fn {init.tcl auto.tcl tclIndex} {
    incr n
    set p [file join $tcl_library $fn]
    if {[catch {set f [open $p r]} e]} { lappend fails "open $fn: $::errorCode"; continue }
    fconfigure $f -eofchar "\x1a {}"
    if {[catch {read $f} e]} { lappend fails "read $fn: $::errorCode $e" }
    close $f
  }
}
list $n [llength $fails] [lrange $fails 0 4]
''' % N_READ)
out["A_tcl_read"] = res
out["A_secs"] = round(time.time() - t0, 1)
r.destroy(); del r

# B. Bare interpreter creation: tk.Tk() then destroy, nothing else.
def create_loop(n):
    fails = collections.Counter(); first = []
    for i in range(n):
        try:
            x = tk.Tk(); x.destroy(); del x
        except tk.TclError as e:
            msg = str(e).splitlines()[0][:80]
            fails[msg] += 1
            if len(first) < 5: first.append((i, str(e)[:400]))
    return dict(fails), first
t0 = time.time()
out["B_tk_create"] = create_loop(N_TK); out["B_secs"] = round(time.time() - t0, 1)

# C. Same, with Python threads connecting to a closed localhost port (what the UI's status poll does).
stop = threading.Event()
def poller():
    while not stop.is_set():
        try:
            socket.create_connection(("127.0.0.1", 1), timeout=2.0).close()
        except OSError:
            pass
ths = [threading.Thread(target=poller, daemon=True) for _ in range(4)]
for t in ths: t.start()
t0 = time.time()
out["C_tk_create_with_threads"] = create_loop(N_TK); out["C_secs"] = round(time.time() - t0, 1)
stop.set()

print("DIAG140 " + json.dumps(out))
