"""Smoke tests for the Tk device settings page: it builds, prefills, validates and saves."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

tk = pytest.importorskip("tkinter")

from device_registry import DeviceRegistry  # noqa: E402

try:
    import device_settings_window as dsw  # noqa: E402
    _root = tk.Tk()
    _root.withdraw()
except tk.TclError:  # no display in this environment
    pytest.skip("no Tk display", allow_module_level=True)


class _Client:
    def __init__(self, status):
        self.status = status

    def get_status(self, timeout=2.0):
        return self.status


@pytest.fixture(autouse=True)
def chinese_ui():
    # These assertions are the Chinese wording; the Monitor UI defaults to English (#106).
    import ui_i18n
    ui_i18n.set_language("zh")


@pytest.fixture
def window(tmp_path):
    path = tmp_path / "devices.json"
    win = dsw.DeviceSettingsWindow(_root, client=_Client({"status": "error"}), path=path,
                                   restart_core=lambda: {"status": "ok"})
    win.update()
    try:
        yield win, path
    finally:
        win.destroy()


def test_missing_file_is_prefilled_with_nine_devices(window):
    win, path = window

    assert len(win._rows) == 9
    assert not path.exists()
    assert "預填 B518 九台" in win.lbl_status.cget("text")


def test_save_with_an_error_writes_nothing_and_shows_it_on_the_row(window):
    win, path = window
    win._rows[2]["vars"]["kvm_ip"].set("192.168.132.999")

    win._save()

    assert not path.exists()
    assert "有錯誤" in win.lbl_status.cget("text")
    assert win._rows[2]["error"].cget("text") != ""


def test_save_writes_a_valid_list_that_core_loads(window):
    win, path = window

    win._save()

    assert path.exists()
    assert len(DeviceRegistry.load(path).entries()) == 9
    assert "需重啟 Core" in win.lbl_status.cget("text")


def test_restart_is_refused_while_unsaved_changes_exist(window):
    win, _ = window
    win._rows[0]["vars"]["channels"].set("8")
    assert win.dirty is True

    win._restart()

    assert "請先儲存" in win.lbl_status.cget("text")


def test_channels_entry_accepts_digits_and_rejects_letters(window):
    win, _ = window
    entry = win._rows[0]["widgets"][4]
    command = str(entry.cget("validatecommand")).split()[0]

    assert win.tk.call(command, "12") == 1
    assert win.tk.call(command, "1a") == 0


def test_probe_result_is_written_only_to_the_row_it_belongs_to(window):
    win, _ = window
    stale = win._rows[1]
    win._rows[0]["selected"].set(True)
    win._delete_selected()  # rebuilds every remaining row, so `stale` is no longer on the page
    result = dsw.editor.ProbeResult(False, None, "無法連線: test")

    win._show_probe(stale, result)  # must not raise on destroyed widgets or write to a shifted row

    assert all(entry["probe"].cget("text") == "" for entry in win._rows)
    win._show_probe(win._rows[1], result)
    assert win._rows[1]["probe"].cget("text") == "無法連線: test"
    assert win._rows[0]["probe"].cget("text") == ""
