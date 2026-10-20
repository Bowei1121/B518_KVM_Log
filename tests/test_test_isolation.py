"""Tests must not touch the operator's real ``~/Documents/template`` folder.

The real home is resolved through the password database, not ``HOME``, so the check
still points at the operator folder while the autouse fixture redirects ``HOME``.
"""

import os
import pwd
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_registry import default_registry_path  # noqa: E402
from match_diagnostics import MatchDiagnostics  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402


def _real_template_root() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / "Documents" / "template"


def _snapshot(root: Path):
    if not root.exists():
        return {}
    entries = {}
    for path in root.rglob("*"):
        stat = path.stat()
        entries[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return entries


def test_default_paths_point_into_isolated_home(isolated_user_home):
    assert TemplateCatalog().root == isolated_user_home / "Documents" / "template"
    assert default_registry_path() == isolated_user_home / "Documents" / "template" / "devices.json"
    assert _real_template_root() != TemplateCatalog().root


def test_diagnostic_run_leaves_real_template_root_untouched():
    real_root = _real_template_root()
    before = _snapshot(real_root)

    recorder = MatchDiagnostics(TemplateCatalog().root, "FCT-01")
    recorder.record("dock_icon", 0.9, 0.8, np.zeros((40, 40, 3), dtype=np.uint8), (1, 1, 10, 10))
    recorder.finalize(True, "check")
    assert MatchDiagnostics.load(TemplateCatalog().root, "FCT-01")["ok"] is True

    assert _snapshot(real_root) == before
