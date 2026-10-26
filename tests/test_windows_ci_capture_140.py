"""Windows CI must not let pytest swap fds 0/1/2 while Tk roots are alive (issue #140).

Tcl on Windows caches the std handles of the first interpreter in a thread, and its file layer hands back
an existing channel when a newly opened file gets the same HANDLE value (win/tclWinChan.c,
TclWinOpenFileChannel). pytest's default --capture=fd closes and replaces the handles behind fds 0/1/2
around every test, so the cached values go stale and get reused by the next init.tcl / tk.tcl open.
That is the intermittent "couldn't read file .../init.tcl: No error". --capture=sys leaves the fds alone.
"""

import shlex
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-windows.yml"


def _pytest_commands():
    runs = [line.strip()[len("run:"):].strip() for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("run:")]
    return [shlex.split(run) for run in runs if "pytest" in shlex.split(run)]


def test_windows_test_step_runs_pytest_with_sys_level_capture():
    commands = _pytest_commands()
    assert len(commands) == 1, commands
    assert "--capture=sys" in commands[0]
