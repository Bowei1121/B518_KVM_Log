"""Device settings page core: prefill, row validation, backup-then-save, probes and restart rules."""

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

import device_list_editor as editor  # noqa: E402
from device_registry import DeviceRegistry  # noqa: E402


def _row(device, station, ip, channels=4):
    return {"device": device, "station": station, "kvm_ip": ip, "channels": channels}


def _base_rows():
    return [_row("DFU-01", "DFU", "192.168.132.70"), _row("FCT-01", "FCT", "192.168.132.77"),
            _row("BT-01", "BT", "192.168.132.78")]


# --- prefill ---------------------------------------------------------------

def test_missing_file_prefills_the_nine_b518_devices(tmp_path):
    state = editor.load_state(tmp_path / "devices.json")

    assert state.prefilled is True
    assert len(state.rows) == 9
    assert [r["device"] for r in state.rows][-2:] == ["FCT-01", "BT-01"]
    assert all(r["channels"] == 4 for r in state.rows)
    assert editor.validate_rows(state.rows) == {}


def test_existing_file_is_loaded_not_prefilled(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"BT-01": {"station": "BT", "kvm_ip": "10.0.0.5", "channels": 2}}), encoding="utf-8")

    state = editor.load_state(path)

    assert state.prefilled is False
    assert state.rows == [{"device": "BT-01", "station": "BT", "kvm_ip": "10.0.0.5", "channels": 2}]


def test_unreadable_file_is_reported_not_silently_replaced(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text("{not json", encoding="utf-8")

    state = editor.load_state(path)

    assert state.rows == []
    assert "無法讀取" in state.load_error


# --- row validation --------------------------------------------------------

def test_valid_rows_have_no_errors():
    assert editor.validate_rows(_base_rows()) == {}


def test_empty_list_is_a_form_error():
    assert editor.validate_rows([]) == {editor.FORM_ERROR: ["設備清單不可為空"]}


@pytest.mark.parametrize("row, fragment", [
    (_row("", "DFU", "192.168.132.70"), "設備 ID 不可為空"),
    (_row("DFU-1", "DFU", "192.168.132.70"), "<站別>-<兩位數>"),
    (_row("DFU-09", "FCT", "192.168.132.70"), "does not match station"),
    (_row("DFU-09", "DFU", "192.168.132.300"), "not a valid IPv4"),
    (_row("DFU-09", "DFU", "192.168.132.70", channels="four"), "channels must be a positive integer"),
    (_row("DFU-09", "DFU", "192.168.132.70", channels=0), "channels must be a positive integer"),
    (_row("DFU-09", "XYZ", "192.168.132.70"), "station must be one of"),
])
def test_each_bad_field_is_reported_on_its_row(row, fragment):
    errors = editor.validate_rows(_base_rows() + [row])

    assert fragment in " ".join(errors[3])
    assert set(errors) == {3}


def test_duplicate_id_is_reported_on_the_later_row():
    rows = _base_rows() + [_row("BT-01", "BT", "192.168.132.79")]

    errors = editor.validate_rows(rows)

    assert "設備 ID 重複" in errors[3][0]
    assert 0 not in errors and 2 not in errors


def test_duplicate_ip_is_reported_on_the_row_that_repeats_it():
    rows = _base_rows()
    rows[1]["kvm_ip"] = rows[0]["kvm_ip"]

    errors = editor.validate_rows(rows)

    assert "also used by DFU-01" in " ".join(errors[1])


def test_text_channels_from_the_entry_are_coerced_when_numeric():
    rows = _base_rows()
    rows[0]["channels"] = " 4 "

    assert editor.validate_rows(rows) == {}
    assert editor.to_registry_json(rows)["DFU-01"]["channels"] == 4


# --- save ------------------------------------------------------------------

def test_save_refuses_invalid_rows_and_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text('{"X": 1}', encoding="utf-8")
    rows = _base_rows()
    rows[0]["channels"] = "bad"

    with pytest.raises(editor.SaveRefused) as excinfo:
        editor.save_rows(rows, path)

    assert 0 in excinfo.value.errors
    assert path.read_text(encoding="utf-8") == '{"X": 1}'
    assert list(tmp_path.iterdir()) == [path]


def test_save_backs_up_the_old_file_with_a_timestamped_name(tmp_path):
    path = tmp_path / "devices.json"
    old = json.dumps({"BT-01": {"station": "BT", "kvm_ip": "10.0.0.5", "channels": 2}})
    path.write_text(old, encoding="utf-8")

    backup = editor.save_rows(_base_rows(), path, now=1_700_000_000)

    assert backup is not None
    assert backup.name.startswith("devices-backup-") and backup.suffix == ".json"
    assert backup.read_text(encoding="utf-8") == old


def test_first_save_has_no_backup_and_the_result_loads_in_core(tmp_path):
    path = tmp_path / "devices.json"

    backup = editor.save_rows(_base_rows(), path)

    assert backup is None
    registry = DeviceRegistry.load(path)
    assert [e.device_id for e in registry.entries()] == ["DFU-01", "FCT-01", "BT-01"]
    assert registry.get("DFU-01").kvm_ip == "192.168.132.70"
    assert not list(tmp_path.glob("*.tmp"))


def test_saved_b518_prefill_is_accepted_by_core(tmp_path):
    path = tmp_path / "devices.json"
    editor.save_rows(editor.b518_plan_rows(), path)

    assert len(DeviceRegistry.load(path).entries()) == 9


# --- connection probe --------------------------------------------------------

class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_reports_reachable_with_latency():
    result = editor.probe_kvm("192.168.132.70", connect=lambda addr, timeout: _Conn())

    assert result.ok is True
    assert result.latency_ms is not None and result.latency_ms >= 0


def test_probe_reports_timeout_and_refusal_without_raising():
    def timeout(addr, timeout):
        raise socket.timeout()

    def refused(addr, timeout):
        raise ConnectionRefusedError(61, "refused")

    slow = editor.probe_kvm("192.168.132.70", connect=timeout)
    closed = editor.probe_kvm("192.168.132.70", connect=refused)

    assert slow.ok is False and "逾時" in slow.message
    assert closed.ok is False and "無法連線" in closed.message


# --- restart rule ------------------------------------------------------------

def test_restart_is_refused_while_any_device_is_busy_and_names_it():
    stations = [{"device": "DFU-01", "busy": False}, {"device": "BT-01", "busy": True}]

    message = editor.restart_refusal(stations)

    assert message is not None and "BT-01" in message and "DFU-01" not in message


def test_restart_is_allowed_when_all_devices_are_idle():
    assert editor.restart_refusal([{"device": "DFU-01", "busy": False}]) is None
