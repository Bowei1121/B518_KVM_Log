"""core_status carries is_heartbeat_not_found for the Monitor UI (issue #130); detection belongs to #125."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_heartbeat_commands import _send, serve  # noqa: F401
from core_protocol import build_request
from protocol_helpers import LogSolutionKVM


def test_core_status_stations_carry_is_heartbeat_not_found_false(serve):
    srv = serve(LogSolutionKVM(state="standby"))
    resp = _send(srv, build_request("core_status", "core-nf", ""))
    assert resp["status"] == "ok", resp
    stations = resp["data"]["stations"]
    assert stations and all(row["is_heartbeat_not_found"] is False for row in stations)


