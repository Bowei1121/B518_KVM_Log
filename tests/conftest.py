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


@pytest.fixture(autouse=True)
def isolated_user_home(tmp_path_factory, monkeypatch):
    # Own sibling directory, not inside tmp_path: tests may list their tmp_path contents.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(DEVICES_FILE_ENV, raising=False)
    return home
