# -*- coding: utf-8 -*-
"""Version, build label and bundled-file paths for the Monitor UI (issue #115).

``VERSION`` (this folder) is edited by hand. The build hash and date come from ``_build_info.py``, which
``tools/write_build_info.py`` writes in CI before PyInstaller runs; a local source run has none, so it shows ``dev``.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "ATE - LabVIEW Bridge Monitor"
_HOST_APP_DIR = Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Folder that holds the bundled assets: ``sys._MEIPASS`` in the frozen exe, the host-app folder otherwise."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return _HOST_APP_DIR


def asset_path(name: str) -> Path:
    """Path of a file under ``host-app/assets`` (or its copy inside the frozen bundle)."""
    return resource_dir() / "assets" / name


def _read_version_file() -> str:
    try:
        return (_HOST_APP_DIR / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def _build_info() -> tuple:
    try:
        import _build_info  # type: ignore[import-not-found]  # written by tools/write_build_info.py in CI
    except ImportError:
        return None, None, None
    return (
        getattr(_build_info, "VERSION", None),
        getattr(_build_info, "BUILD_HASH", None),
        getattr(_build_info, "BUILD_DATE", None),
    )


def is_ci_build() -> bool:
    """True when ``_build_info.py`` was generated for this build (the CI and on-site builds)."""
    return _build_info()[1] is not None


def version() -> str:
    """Version shown in the title and the Log, e.g. ``0.1.00``."""
    version_from_build, _hash, _date = _build_info()
    return version_from_build or _read_version_file()


def build_label() -> str:
    """``build abc1234，2026-10-22`` on a CI build, ``build dev`` on a local run."""
    _version, build_hash, build_date = _build_info()
    if not build_hash:
        return "build dev"
    return f"build {build_hash}, {build_date}" if build_date else f"build {build_hash}"


def title_text() -> str:
    return f"{APP_NAME} v{version()}"


def about_text() -> str:
    return f"{title_text()}（{build_label()}）"
