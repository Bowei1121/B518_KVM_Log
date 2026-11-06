"""Pop-out picture/control consistency (PR #187 review), without a real Tk window (Tk is slow/flaky on macOS CI).

The logic under test only touches plain attributes, so a StreamerWindow built without __init__ is enough.
"""

from types import SimpleNamespace

from stream_view import StreamerWindow


def _window(mode="control", mapped=True):
    win = object.__new__(StreamerWindow)
    sent = []
    win.input_sender = lambda m, p: sent.append((m, p))
    win._mode = SimpleNamespace(get=lambda: mode)
    win._map = (1.0, 0, 0, 200, 100) if mapped else None
    win._buttons = 0
    win._mods = 0
    win._keys = []
    win._last_abs = None
    win._last_move = 0.0
    return win, sent


def test_control_requires_a_valid_picture():
    win, _ = _window(mapped=True)
    assert win._controlling()
    win._map = None
    assert not win._controlling()
    assert not _window(mode="monitor")[0]._controlling()


def test_release_all_sends_key_and_mouse_release():
    win, sent = _window()
    win._keys = [4]
    win._mods = 2
    win._buttons = 1
    win._last_abs = (5, 6)
    win._release_all()
    assert ("absMouseReport", {"x": 5, "y": 6, "buttons": 0}) in sent
    assert ("keyboardReport", {"modifier": 0, "keys": []}) in sent
    assert win._buttons == 0 and win._keys == [] and win._mods == 0


def test_input_is_ignored_without_a_picture():
    win, sent = _window(mapped=False)
    win._on_key_press(SimpleNamespace(keysym="a", state=0))
    win._on_wheel(SimpleNamespace(delta=120))
    win._on_motion(SimpleNamespace(x=1, y=1, state=0))
    assert sent == []
