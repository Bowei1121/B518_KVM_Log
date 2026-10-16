import json
import sys
from pathlib import Path

import pytest

# Ensure host-app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_registry import DeviceRegistry, DeviceRegistryError


VALID = {
    "DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4},
    "FCT-01": {"station": "FCT", "kvm_ip": "192.168.132.77", "channels": 4},
    "BT-01": {"station": "BT", "kvm_ip": "192.168.132.78", "channels": 4},
}


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "devices.json"
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def _errors_for(tmp_path: Path, data) -> list[str]:
    with pytest.raises(DeviceRegistryError) as excinfo:
        DeviceRegistry.load(_write(tmp_path, data))
    return excinfo.value.errors


def test_loads_valid_list_and_looks_up_by_exact_id(tmp_path):
    registry = DeviceRegistry.load(_write(tmp_path, VALID))

    dfu = registry.get("DFU-01")
    assert dfu is not None
    assert (dfu.station, dfu.kvm_ip, dfu.channels) == ("DFU", "192.168.132.70", 4)
    assert registry.get("dfu-01") is None
    assert registry.get("DFU-99") is None


def test_entries_keep_file_order(tmp_path):
    registry = DeviceRegistry.load(_write(tmp_path, VALID))

    assert [e.device_id for e in registry.entries()] == ["DFU-01", "FCT-01", "BT-01"]


def test_missing_file_is_rejected_with_path(tmp_path):
    missing = tmp_path / "nope.json"

    with pytest.raises(DeviceRegistryError) as excinfo:
        DeviceRegistry.load(missing)

    assert any("not found" in e and str(missing) in e for e in excinfo.value.errors)


def test_invalid_json_is_rejected(tmp_path):
    errors = _errors_for(tmp_path, "{not json")

    assert len(errors) == 1
    assert errors[0].startswith("invalid JSON")


def test_top_level_must_be_object(tmp_path):
    assert _errors_for(tmp_path, ["DFU-01"]) == [
        "device list must be a JSON object keyed by device ID"
    ]


def test_empty_list_is_rejected(tmp_path):
    assert _errors_for(tmp_path, {}) == ["device list is empty"]


def test_duplicate_device_id_is_rejected(tmp_path):
    text = (
        '{"DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4},'
        ' "DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.71", "channels": 4}}'
    )

    errors = _errors_for(tmp_path, text)

    assert "duplicate device ID 'DFU-01'" in errors


def test_duplicate_ip_names_both_devices(tmp_path):
    data = {
        "DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4},
        "DFU-02": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4},
    }

    errors = _errors_for(tmp_path, data)

    assert any(e.startswith("DFU-02:") and "192.168.132.70" in e and "DFU-01" in e for e in errors)


@pytest.mark.parametrize(
    "device_id",
    ["DFU1", "DFU-1", "DFU-001", "dfu-01", "DFU_01", "DFU-01 "],
)
def test_malformed_device_id_is_rejected(tmp_path, device_id):
    data = {device_id: {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4}}

    errors = _errors_for(tmp_path, data)

    assert any(e.startswith(f"{device_id}:") and "<站別>-<兩位數>" in e for e in errors)


def test_id_prefix_must_match_station(tmp_path):
    data = {"FCT-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": 4}}

    errors = _errors_for(tmp_path, data)

    assert any("does not match station" in e for e in errors)


@pytest.mark.parametrize("station", ["XYZ", "", None, 1])
def test_unknown_station_is_rejected(tmp_path, station):
    data = {"DFU-01": {"station": station, "kvm_ip": "192.168.132.70", "channels": 4}}

    errors = _errors_for(tmp_path, data)

    assert any("station must be one of DFU, FCT, BT" in e for e in errors)


@pytest.mark.parametrize("ip", ["192.168.132", "192.168.132.256", "localhost", "", None, 70])
def test_invalid_ipv4_is_rejected(tmp_path, ip):
    data = {"DFU-01": {"station": "DFU", "kvm_ip": ip, "channels": 4}}

    errors = _errors_for(tmp_path, data)

    assert any(e.startswith("DFU-01:") and "kvm_ip" in e for e in errors)


@pytest.mark.parametrize("channels", [0, -1, 2.5, "4", None, True])
def test_invalid_channels_are_rejected(tmp_path, channels):
    data = {"DFU-01": {"station": "DFU", "kvm_ip": "192.168.132.70", "channels": channels}}

    errors = _errors_for(tmp_path, data)

    assert any(e == "DFU-01: channels must be a positive integer" for e in errors)


def test_entry_must_be_object(tmp_path):
    errors = _errors_for(tmp_path, {"DFU-01": "192.168.132.70"})

    assert errors == ["DFU-01: entry must be an object"]


def test_all_errors_are_reported_together(tmp_path):
    data = {
        "DFU-01": {"station": "DFU", "kvm_ip": "bad", "channels": 0},
        "fct-02": {"station": "FCT", "kvm_ip": "192.168.132.70", "channels": 4},
    }

    errors = _errors_for(tmp_path, data)

    assert any(e.startswith("DFU-01:") and "kvm_ip" in e for e in errors)
    assert any(e.startswith("DFU-01:") and "channels" in e for e in errors)
    assert any(e.startswith("fct-02:") and "device ID" in e for e in errors)
    assert len(errors) >= 3
