"""Pop-out frame source consistency (PR #187 third review): one (device, image) snapshot, read once."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))

from ui_app import frame_for_device  # noqa: E402


def test_only_the_bound_devices_image_is_returned():
    assert frame_for_device(("BT-01", "img1"), "BT-01") == "img1"
    assert frame_for_device(("BT-02", "img2"), "BT-01") is None
    assert frame_for_device(None, "BT-01") is None


def test_reader_holding_a_snapshot_never_sees_a_mixed_pair():
    cache = {"frame": ("BT-01", "img1")}
    held = cache["frame"]          # what a getter read before the worker published again
    cache["frame"] = ("BT-02", "img2")
    assert frame_for_device(held, "BT-01") == "img1"      # still the matching pair
    assert frame_for_device(cache["frame"], "BT-01") is None
