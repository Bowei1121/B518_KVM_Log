"""Guard Windows CI capture for issue #140.

Default fd capture swaps std handles behind fds 0/1/2 around each test while Tk
roots stay alive; sys capture leaves those handles unchanged. On windows-2025,
the four-module step went from Tk init failures in 5/19 runs to 0/14 with sys
capture. The exact Tcl mechanism is unproven. This static workflow check and the
win32 capture check in tests/conftest.py guard the workaround.
tests/capture_default_140.py makes sys the Windows default when capture is not given explicitly.
"""

import re
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
        lexer = shlex.shlex(run, posix=True, punctuation_chars="&;|")
        lexer.whitespace_split = True
        segments = [[]]
        for token in lexer:
            if token in ("&&", ";", "||"):
                segments.append([])
            else:
                segments[-1].append(token)
        for tokens in segments:
            if not tokens:
                continue
            interpreter = re.fullmatch(r"(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?",
                                       Path(tokens[0]).name)
            if tokens[0] == "pytest" or (
                interpreter and any(tokens[i:i + 2] == ["-m", "pytest"]
                                    for i in range(1, len(tokens) - 1))
            ):
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
        elif re.fullmatch(r"-[qvxlh]*s[qvxlhs]*", arg):
            capture = "no"
    return capture


@pytest.mark.parametrize(("args", "expected"), [
    ("--capture=sys --capture=fd", "fd"),
    ("--capture=fd --capture=sys", "sys"),
    ("--capture sys", "sys"),
    ("", "fd"),
    ("pytest -rs", "fd"),
    ("pytest -qs", "no"),
    ("pytest -vxs", "no"),
    ("pytest -kslow --capture=sys", "sys"),
    ("pytest -Wignore::ResourceWarning", "fd"),
    ("pytest -pno:capture", "fd"),
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


def test_windows_test_step_runs_pytest_with_safe_explicit_capture():
    commands = _pytest_commands()
    assert commands
    for command in commands:
        assert _effective_capture(command) in {"sys", "no", "tee-sys"}, command
    assert any("tests/test_windows_ci_capture_140.py" in command for command in commands)


def test_windows_fd_capture_is_rejected():
    from conftest import check_capture_method

    with pytest.raises(pytest.UsageError, match=r"issue #140:.*--capture=sys"):
        check_capture_method("win32", "fd")
    for platform, capture in [("win32", "sys"), ("win32", "no"), ("linux", "fd"), ("darwin", "fd")]:
        check_capture_method(platform, capture)


def test_running_session_has_safe_capture(request):
    if sys.platform == "win32":
        assert request.config.getoption("capture", "no") != "fd"


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("explicit_capture", [None, "fd", "sys", "no", "tee-sys"])
def test_default_capture(platform, explicit_capture):
    from capture_default_140 import default_capture

    expected = "sys" if platform == "win32" and explicit_capture is None else None
    assert default_capture(platform, explicit_capture) == expected


@pytest.mark.parametrize("capture", ["fd", "sys", "no", "tee-sys"])
def test_conftest_hook_rejects_fd_on_win32(monkeypatch, capture):
    import conftest

    class Config:
        def getoption(self, name, default=None):
            return capture

    monkeypatch.setattr(sys, "platform", "win32")
    if capture == "fd":
        with pytest.raises(pytest.UsageError):
            conftest.pytest_configure(Config())
    else:
        conftest.pytest_configure(Config())


@pytest.mark.parametrize(("command", "capture"), [
    ("cd tests && python -m pytest --capture=fd .", "fd"),
    ("python -X utf8 -m pytest --capture=fd", "fd"),
    ("python3 -u -m pytest -s", "no"),
    ("py -m pytest --capture=tee-sys", "tee-sys"),
    ("pytest -qs", "no"),
    ("echo start; pytest --capture=no || echo failed", "no"),
])
def test_shell_pytest_commands(command, capture):
    commands = _pytest_commands("run: " + command)
    assert len(commands) == 1
    assert _effective_capture(commands[0]) == capture


def _run_capture_session(tmp_path, flags, *, platform=None, expected="no", addopts=""):
    import os
    import subprocess

    root = WORKFLOW.parents[2]
    probe = tmp_path / "test_capture_probe.py"
    probe.write_text(
        "def test_capture(request):\n"
        f"    assert request.config.getoption('capture', 'no') == {expected!r}\n",
        encoding="utf-8",
    )
    # Explicitly load the repository conftest even though the probe is outside tests/.
    args = ["-c", str(root / "pytest.ini"), "--rootdir", str(root),
            "-p", "conftest", "-q", *flags, str(probe)]
    script = (
        "import sys, pytest, io; "
        + ("io._WindowsConsoleIO = type('WindowsConsoleStub', (), {}); "
           if platform == "win32" and sys.platform != "win32" else "")
        + (f"sys.platform = {platform!r}; " if platform else "")
        + f"raise SystemExit(pytest.main({args!r}))"
    )
    env = dict(os.environ, PYTEST_ADDOPTS=addopts, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
                          text=True, capture_output=True, timeout=30)


def test_without_capture_plugin(tmp_path):
    result = _run_capture_session(tmp_path, ["-p", "no:capture"])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("flags", "expected", "code", "addopts"), [
    ([], "sys", 0, ""),
    (["-rs"], "sys", 0, ""),
    (["-Wignore::ResourceWarning"], "sys", 0, ""),
    ([], "sys", 0, "-rs"),
    (["--capture=fd"], "fd", 4, ""),
    (["-s"], "no", 0, ""),
    (["-qs"], "no", 0, ""),
    (["--capture=no"], "no", 0, ""),
    (["--capture=tee-sys"], "tee-sys", 0, ""),
    (["--capture=sys"], "sys", 0, ""),
    ([], "fd", 4, "--capture=fd"),
    (["-p", "no:capture"], "no", 0, ""),
])
def test_windows_capture_subprocess(tmp_path, flags, expected, code, addopts):
    result = _run_capture_session(tmp_path, flags, platform="win32",
                                  expected=expected, addopts=addopts)
    assert result.returncode == code, result.stdout + result.stderr
    if code == 4:
        assert "issue #140:" in result.stderr


def test_real_platform_capture_default_subprocess(tmp_path):
    expected = "sys" if sys.platform == "win32" else "fd"
    result = _run_capture_session(tmp_path, [], expected=expected)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("commands", "valid"), [
    ("pytest -s tests/test_windows_ci_capture_140.py\n  pytest --capture=tee-sys other.py", True),
    ("pytest -qs other.py\n  pytest --capture=no tests/test_windows_ci_capture_140.py", True),
    ("pytest tests/test_windows_ci_capture_140.py", False),
    ("pytest --capture=fd tests/test_windows_ci_capture_140.py", False),
    ("pytest --capture=sys other.py", False),
])
def test_workflow_capture_and_guard_policy(tmp_path, monkeypatch, commands, valid):
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("run: |\n  " + commands + "\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WORKFLOW", workflow)
    if valid:
        test_windows_test_step_runs_pytest_with_safe_explicit_capture()
    else:
        with pytest.raises(AssertionError):
            test_windows_test_step_runs_pytest_with_safe_explicit_capture()
