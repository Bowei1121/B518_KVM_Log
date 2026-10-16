"""Command-to-connection routing in the Monitor UI client (#20).

Every Core command travels on exactly one named connection. A command with no
route is a programming error and must raise, not quietly use the status connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOST_APP_DIR = Path(__file__).resolve().parents[1] / "host-app"
if str(_HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_HOST_APP_DIR))

from ui_app import CoreServiceClient  # noqa: E402


def test_unknown_command_raises_instead_of_using_status_connection() -> None:
    client = CoreServiceClient(host="127.0.0.1", port=1)  # nothing listens here; must not be reached

    with pytest.raises(ValueError, match="bogus"):
        client.send_request("bogus")
