"""Windows DPI awareness and Tk scaling from issue #107.

All checks use mocks, so they run on any OS. The real behaviour on a 100% / 125% / 150% screen is a field check (docs/FIELD_VERIFICATION.md H1).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))

import ui_app  # noqa: E402


def _fake_windll(shcore_result=0, shcore_raises=None, user32_result=1, user32_raises=None):
    windll = MagicMock(name="windll")
    if shcore_raises is not None:
        windll.shcore.SetProcessDpiAwareness.side_effect = shcore_raises
    else:
        windll.shcore.SetProcessDpiAwareness.return_value = shcore_result
    if user32_raises is not None:
        windll.user32.SetProcessDPIAware.side_effect = user32_raises
    else:
        windll.user32.SetProcessDPIAware.return_value = user32_result
    return windll


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


# ---------------------------------------------------------------- DPI awareness

def test_non_windows_never_touches_windll(mac):
    windll = _fake_windll()
    assert ui_app.enable_windows_dpi_awareness(windll) == "skipped"
    assert windll.mock_calls == []


def test_windows_uses_system_dpi_aware_shcore_first(windows):
    windll = _fake_windll(shcore_result=0)
    assert ui_app.enable_windows_dpi_awareness(windll) == "shcore"
    windll.shcore.SetProcessDpiAwareness.assert_called_once_with(1)  # PROCESS_SYSTEM_DPI_AWARE
    windll.user32.SetProcessDPIAware.assert_not_called()


def test_shcore_raising_falls_back_to_user32(windows):
    windll = _fake_windll(shcore_raises=OSError("shcore missing"), user32_result=1)
    assert ui_app.enable_windows_dpi_awareness(windll) == "user32"
    windll.user32.SetProcessDPIAware.assert_called_once_with()


def test_shcore_hresult_error_falls_back_to_user32(windows):
    # E_ACCESSDENIED (0x80070005): awareness was already set elsewhere, so shcore reports failure.
    windll = _fake_windll(shcore_result=0x80070005, user32_result=1)
    assert ui_app.enable_windows_dpi_awareness(windll) == "user32"
    windll.user32.SetProcessDPIAware.assert_called_once_with()


def test_both_calls_failing_reports_failed_without_raising(windows):
    windll = _fake_windll(shcore_raises=OSError("x"), user32_raises=OSError("y"))
    assert ui_app.enable_windows_dpi_awareness(windll) == "failed"


def test_user32_returning_false_reports_failed(windows):
    windll = _fake_windll(shcore_raises=OSError("x"), user32_result=0)
    assert ui_app.enable_windows_dpi_awareness(windll) == "failed"


# ---------------------------------------------------------------- Tk scaling

def _fake_root(dpi):
    root = MagicMock(name="root")
    root.winfo_fpixels.return_value = float(dpi)
    return root


@pytest.mark.parametrize("dpi, expected", [(96, 96 / 72), (120, 120 / 72), (144, 2.0), (216, 3.0)])
def test_windows_tk_scaling_follows_real_dpi(windows, dpi, expected):
    root = _fake_root(dpi)
    ui_app.apply_windows_tk_scaling(root)
    root.winfo_fpixels.assert_called_once_with("1i")
    root.tk.call.assert_called_once_with("tk", "scaling", pytest.approx(expected))


def test_non_windows_tk_scaling_is_untouched(mac):
    root = _fake_root(144)
    ui_app.apply_windows_tk_scaling(root)
    root.winfo_fpixels.assert_not_called()
    root.tk.call.assert_not_called()
