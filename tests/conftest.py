"""Shared pytest fixtures.

Every test runs with ``HOME`` pointed at a per-test directory. The host app derives its
default template root (``~/Documents/template``), device list and match diagnostics from
``Path.home()``, so this keeps tests from writing into the real operator folder and keeps
parallel pytest runs from racing on the same ``_captures`` directory.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_registry import DEVICES_FILE_ENV  # noqa: E402


def check_capture_method(platform: str, capture: str) -> None:
    if platform == "win32" and capture == "fd":
        raise pytest.UsageError(
            "issue #140: on Windows run pytest with --capture=sys (or -s); fd capture breaks Tk init"
        )


def pytest_configure(config):
    check_capture_method(sys.platform, config.getoption("capture", "no"))


@pytest.fixture(autouse=True)
def isolated_user_home(tmp_path_factory, monkeypatch):
    # Own sibling directory, not inside tmp_path: tests may list their tmp_path contents.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    # Windows ignores HOME: Path.home() reads USERPROFILE, and the Monitor UI prefs live under APPDATA.
    # Without these, a Windows test run rewrites the operator's real ui_prefs.json (issue #107 review).
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.delenv(DEVICES_FILE_ENV, raising=False)
    return home


@pytest.fixture(autouse=True)
def monitor_ui_language_default():
    """The Monitor UI language is process-wide: each test starts and ends on the English default (#106)."""
    try:
        import ui_i18n
    except ImportError:  # no Tk in this interpreter: there is no language to reset
        yield
        return
    ui_i18n.set_language(ui_i18n.DEFAULT_LANGUAGE)
    yield
    ui_i18n.set_language(ui_i18n.DEFAULT_LANGUAGE)
