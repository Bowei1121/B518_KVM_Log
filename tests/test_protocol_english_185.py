"""Protocol text stays English, including localized external exception details."""

import ast
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))
from tcp_server import TcpJsonServer


@pytest.mark.parametrize("message", ["裝置未回應", "HID failed: 裝置錯誤", "Login failed (HTTP 401)"])
def test_envelope_keeps_error_codes_and_english_text(message):
    # Exercise the real serializer without binding a socket.
    server = TcpJsonServer.__new__(TcpJsonServer)
    server._commands = {}
    reply = json.loads(server._encode_envelope(
        time.monotonic(), "en185", "DFU-01", "input", "error", "hid_failed", message, {}))
    assert reply["error"] == "hid_failed"
    assert reply["message"] == (message if message.isascii() else
                                "Command failed; see Core logs for details")


@pytest.mark.parametrize("filename", [
    "host-app/monitor_flow.py", "host-app/dfu_flow.py", "host-app/bt_flow.py",
    "host-app/hid_session.py", "host-app/jetkvm_core.py",
    "host-app/log_solution_heartbeat.py", "host-app/tcp_server.py",
    "tools/fake_core/fake_core.py",
])
def test_protocol_text_literals_are_english(filename):
    tree = ast.parse((ROOT / filename).read_text())
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "")
            if name in {"FlowError", "KVMLoginFailed", "KVMRpcNotOpen", "KVMNoSignal"}:
                texts.extend(node.args)
            texts.extend(kw.value for kw in node.keywords if kw.arg in {"message", "failure", "missing"})
        elif isinstance(node, ast.Dict):
            texts.extend(value for key, value in zip(node.keys, node.values)
                         if isinstance(key, ast.Constant) and key.value == "message")
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "HEARTBEAT_STOPPED_MESSAGE"
                for target in node.targets):
            texts.append(node.value)
    for text in texts:
        for node in ast.walk(text):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value.isascii(), f"{filename}:{node.lineno}: {node.value}"
