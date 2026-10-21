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


# ---------------------------------------------------------------- call order and wiring (review of PR #110)

def test_main_declares_dpi_awareness_before_creating_the_tk_root(monkeypatch):
    calls = []
    monkeypatch.setattr(ui_app, "enable_windows_dpi_awareness", lambda *a, **k: calls.append("dpi") or "shcore")

    class FakeUI:
        def __init__(self, **_kw):
            calls.append("tk_root")

        def mainloop(self):
            calls.append("mainloop")

    monkeypatch.setattr(ui_app, "AtlasUI", FakeUI)
    monkeypatch.setattr(sys, "argv", ["ui_app"])
    ui_app.main()
    assert calls == ["dpi", "tk_root", "mainloop"]
    assert ui_app._DPI_AWARENESS_MODE == "shcore"


def test_atlasui_applies_tk_scaling_to_its_own_root(monkeypatch):
    tk = pytest.importorskip("tkinter")
    seen = []
    real = ui_app.apply_windows_tk_scaling
    monkeypatch.setattr(ui_app, "apply_windows_tk_scaling", lambda root: seen.append(root) or real(root))
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    try:
        app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    except tk.TclError:
        pytest.skip("no Tk display")
    try:
        assert seen == [app]
    finally:
        app._file_log.close()
        app.destroy()


def test_windows_startup_writes_the_dpi_path_to_the_log(monkeypatch):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(sys, "platform", "win32")  # the Log line is Windows-only; Tk itself runs fine on macOS
    monkeypatch.setattr(ui_app, "_DPI_AWARENESS_MODE", "user32")
    monkeypatch.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None)
    monkeypatch.setattr(ui_app, "PatternCropper", object)
    try:
        app = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1)
    except tk.TclError:
        pytest.skip("no Tk display")
    try:
        log = app.log_text.get("1.0", "end")
        assert "Windows DPI: user32, tk scaling" in log
    finally:
        app._file_log.close()
        app.destroy()


def test_self_test_passes_in_this_environment():
    assert ui_app.runtime_self_test() == []


def test_self_test_reports_a_dependency_that_will_not_import(monkeypatch):
    import importlib

    real = importlib.import_module

    def broken(name, *a, **k):
        if name == "cv2":
            raise ImportError("DLL load failed")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", broken)
    problems = ui_app.runtime_self_test()
    assert any(p.startswith("import cv2") for p in problems), problems


def test_self_test_flag_exits_non_zero_when_something_is_missing(monkeypatch):
    monkeypatch.setattr(ui_app, "runtime_self_test", lambda: ["import cv2: boom"])
    monkeypatch.setattr(sys, "argv", ["ui_app", "--self-test"])
    with pytest.raises(SystemExit) as exc:
        ui_app.main()
    assert exc.value.code == 1
