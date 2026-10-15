"""Shared helpers for protocol 1.0 socket tests."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from device_registry import DeviceRegistry  # noqa: E402
from tcp_server import TcpJsonServer  # noqa: E402

PROTOCOL = "1.0"


def make_registry() -> DeviceRegistry:
    """Registry with DFU-01..DFU-20 on distinct IPs; tests address devices by these IDs."""
    data = {
        f"DFU-{i:02d}": {"station": "DFU", "kvm_ip": f"192.168.200.{i}", "channels": 4}
        for i in range(1, 21)
    }
    return DeviceRegistry.from_text(json.dumps(data))


def wire(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Fill protocol_version and req_id when a test does not set them."""
    out = dict(obj)
    out.setdefault("protocol_version", PROTOCOL)
    out.setdefault("req_id", f"t-{uuid.uuid4().hex[:8]}")
    out.setdefault("device", "")
    return out


def numbered(device: Any) -> str:
    """Map legacy numeric test device names onto valid DFU-NN IDs."""
    return f"DFU-{int(device):02d}"


class ProbeServer(TcpJsonServer):
    """TcpJsonServer with a device-bound 'probe' command, used to exercise device locks."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.register_handler("probe", lambda req: {"pong": True}, device_bound=True)
