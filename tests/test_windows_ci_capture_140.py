"""Guard Windows CI capture for issue #140.

Default fd capture swaps std handles behind fds 0/1/2 around each test while Tk
roots stay alive; sys capture leaves those handles unchanged. On windows-2025,
the four-module step went from Tk init failures in 5/19 runs to 0/14 with sys
capture. The exact Tcl mechanism is unproven. This static workflow check and the
win32 capture check in tests/conftest.py guard the workaround.
"""

import shlex
import sys
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-windows.yml"


def _pytest_commands(workflow=None):
    lines = (WORKFLOW.read_text(encoding="utf-8") if workflow is None else workflow).splitlines()
    commands = []
    block_indent = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if block_indent is not None and stripped and indent <= block_indent:
            block_indent = None
        if stripped.startswith("run:"):
            run = stripped[len("run:"):].strip()
            if run in ("|", ">", "|-", ">-"):
                block_indent = indent
                continue
        elif block_indent is not None:
            run = stripped
        else:
            continue
        if "pytest" not in run:
            continue
        tokens = shlex.split(run, comments=True)
        if tokens and (tokens[0] == "pytest" or tokens[1:3] == ["-m", "pytest"]):
            commands.append(tokens)
    return commands


def _effective_capture(command):
    capture = "fd"
    args = iter(command)
    for arg in args:
        if arg.startswith("--capture="):
            capture = arg.split("=", 1)[1]
        elif arg == "--capture":
            capture = next(args)
        elif arg == "-s":
            capture = "no"
    return capture


@pytest.mark.parametrize(("args", "expected"), [
    ("--capture=sys --capture=fd", "fd"),
    ("--capture=fd --capture=sys", "sys"),
    ("--capture sys", "sys"),
    ("", "fd"),
    ("--capture=sys -s", "no"),
    ("-s --capture=sys", "sys"),
])
def test_effective_capture_uses_last_option(args, expected):
    assert _effective_capture(shlex.split(args)) == expected


def test_pytest_commands_include_run_blocks():
    workflow = "run: python -m pytest --capture=sys\nrun: |\n  python -m pytest --capture sys\n"
    assert _pytest_commands(workflow) == [
        ["python", "-m", "pytest", "--capture=sys"],
        ["python", "-m", "pytest", "--capture", "sys"],
    ]


def test_windows_test_step_runs_pytest_with_sys_level_capture():
    commands = _pytest_commands()
    assert commands
    for command in commands:
        assert _effective_capture(command) == "sys", command
        assert "tests/test_windows_ci_capture_140.py" in command


def test_windows_fd_capture_is_rejected():
    from conftest import check_capture_method

    with pytest.raises(pytest.UsageError, match=r"issue #140:.*--capture=sys"):
        check_capture_method("win32", "fd")
    for platform, capture in [("win32", "sys"), ("win32", "no"), ("linux", "fd"), ("darwin", "fd")]:
        check_capture_method(platform, capture)


def test_running_session_has_safe_capture(request):
    if sys.platform == "win32":
        assert request.config.getoption("capture") != "fd"
