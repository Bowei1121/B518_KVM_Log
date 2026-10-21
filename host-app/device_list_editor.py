"""Non-GUI core of the Monitor UI device settings page (ticket #10).

Loads, validates, backs up and saves the device list that Core Service reads at
startup. The validation rules are the ones in device_registry, so the page and Core
always agree. Nothing here touches Tk.
"""

import json
import os
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from device_registry import default_registry_path, validate_device_data
from ui_i18n import t

B518_PLANNED_CHANNELS = 4
B518_PLANNED_DEVICES = (
    [(f"DFU-{i:02d}", "DFU", f"192.168.132.{69 + i}") for i in range(1, 8)]
    + [("FCT-01", "FCT", "192.168.132.77"), ("BT-01", "BT", "192.168.132.78")]
)
FORM_ERROR = "__form__"
KVM_HTTP_PORT = 80


@dataclass
class ListState:
    rows: List[Dict[str, Any]]
    prefilled: bool  # True when no file existed and the B518 plan was used
    load_error: str  # empty when the file parsed


def b518_plan_rows() -> List[Dict[str, Any]]:
    return [
        {"device": device, "station": station, "kvm_ip": ip, "channels": B518_PLANNED_CHANNELS}
        for device, station, ip in B518_PLANNED_DEVICES
    ]


def coerce_channels(text: Any) -> Any:
    """Entry text to an int when it is all digits; anything else stays raw so validation names it."""
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    stripped = str(text).strip()
    return int(stripped) if stripped.isdigit() else stripped


def load_state(path: Optional[Path] = None) -> ListState:
    """Rows from the device list file, or the B518 plan when no file exists yet."""
    file_path = Path(path) if path is not None else default_registry_path()
    if not file_path.exists():
        return ListState(rows=b518_plan_rows(), prefilled=True, load_error="")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ListState(rows=[], prefilled=False, load_error=t("ed_read_fail", path=file_path, error=exc))
    if not isinstance(data, dict):
        return ListState(rows=[], prefilled=False, load_error=t("ed_not_object"))
    rows = []
    for device, raw in data.items():
        raw = raw if isinstance(raw, dict) else {}
        rows.append({
            "device": str(device),
            "station": raw.get("station", ""),
            "kvm_ip": raw.get("kvm_ip", ""),
            "channels": raw.get("channels", ""),
        })
    return ListState(rows=rows, prefilled=False, load_error="")


def validate_rows(rows: List[Dict[str, Any]]) -> Dict[Any, List[str]]:
    """Errors keyed by row index; FORM_ERROR for problems that belong to the whole list."""
    if not rows:
        return {FORM_ERROR: [t("ed_empty")]}
    errors: Dict[Any, List[str]] = {}
    first_row_of: Dict[str, int] = {}
    for index, row in enumerate(rows):
        device = str(row.get("device", "")).strip()
        if device == "":
            errors.setdefault(index, []).append(t("ed_id_empty"))
            continue
        if device in first_row_of:
            errors.setdefault(index, []).append(t("ed_id_dup", row=first_row_of[device] + 1))
            continue
        first_row_of[device] = index

    data: Dict[str, Dict[str, Any]] = OrderedDict()
    row_of: Dict[str, int] = {}
    for device, index in first_row_of.items():
        if index in errors:
            continue
        row = rows[index]
        data[device] = {
            "station": str(row.get("station", "")).strip(),
            "kvm_ip": str(row.get("kvm_ip", "")).strip(),
            "channels": coerce_channels(row.get("channels", "")),
        }
        row_of[device] = index

    _entries, messages = validate_device_data(data)
    for message in messages:
        label, _, reason = message.partition(": ")
        if label in row_of:
            errors.setdefault(row_of[label], []).append(reason or message)
        else:
            errors.setdefault(FORM_ERROR, []).append(message)
    return errors


def to_registry_json(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = OrderedDict()
    for row in rows:
        out[str(row["device"]).strip()] = {
            "station": str(row["station"]).strip(),
            "kvm_ip": str(row["kvm_ip"]).strip(),
            "channels": coerce_channels(row["channels"]),
        }
    return out


class SaveRefused(Exception):
    def __init__(self, errors: Dict[Any, List[str]]) -> None:
        super().__init__(t("ed_refused"))
        self.errors = errors


def backup_name(path: Path, now: Optional[float] = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    return path.with_name(f"{path.stem}-backup-{stamp}{path.suffix}")


def save_rows(rows: List[Dict[str, Any]], path: Optional[Path] = None,
              now: Optional[float] = None) -> Optional[Path]:
    """Validate, back up the current file with a timestamped name, then replace it atomically.

    Returns the backup path, or None when there was no file to back up. Refuses without
    touching the file when any row is invalid.
    """
    file_path = Path(path) if path is not None else default_registry_path()
    errors = validate_rows(rows)
    if errors:
        raise SaveRefused(errors)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if file_path.exists():
        backup = backup_name(file_path, now)
        backup.write_bytes(file_path.read_bytes())
    temp = file_path.with_name(file_path.name + ".tmp")
    temp.write_text(json.dumps(to_registry_json(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, file_path)
    return backup


@dataclass
class ProbeResult:
    ok: bool
    latency_ms: Optional[float]
    message: str


def probe_kvm(ip: str, timeout: float = 2.0, port: int = KVM_HTTP_PORT,
              connect: Callable[..., Any] = socket.create_connection) -> ProbeResult:
    """Reachability of a JetKVM: a TCP connect to its web port, timed."""
    started = time.monotonic()
    try:
        with connect((ip, port), timeout=timeout):
            pass
    except socket.timeout:
        return ProbeResult(False, None, t("ed_probe_timeout", seconds=timeout))
    except OSError as exc:
        return ProbeResult(False, None, t("ed_probe_fail", error=exc))
    latency = round((time.monotonic() - started) * 1000, 1)
    return ProbeResult(True, latency, t("ed_probe_ok", ms=latency))


def restart_refusal(stations: List[Dict[str, Any]]) -> Optional[str]:
    """Why Core must not restart now: any device with an action in progress. None when it may."""
    busy = [str(st.get("device", "")) for st in stations if st.get("is_busy")]
    if not busy:
        return None
    return t("ed_restart_busy", names=t("sep_list").join(busy))
