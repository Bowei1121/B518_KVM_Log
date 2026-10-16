"""Small HID helpers shared by the station flows (monitor_start, DFU, BT)."""


def center(x, y, width, height):
    """Click point at the middle of a matched box, in frame pixels."""
    return int(x + width / 2), int(y + height / 2)


def last_hid_error(kvm):
    """The newest JetKVM HID RPC error, or None when the last HID actions succeeded."""
    errors = getattr(kvm, "rpc_errors", [])
    return errors[-1] if errors else None
