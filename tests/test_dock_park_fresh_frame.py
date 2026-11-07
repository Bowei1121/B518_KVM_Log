"""Dock click -> park the pointer -> wait for fresh frames -> match once more on a miss (never a second Dock click)."""

import asyncio
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bt_flow  # noqa: E402
import dfu_flow  # noqa: E402
import hid_session  # noqa: E402
import monitor_flow  # noqa: E402
from dfu_helpers import BUTTON, SyntheticDfuKVM, save_templates  # noqa: E402
from fake_kvm import FakeKVM, make_snapshot  # noqa: E402
from hid_session import FlowError, HidSession  # noqa: E402
from match_diagnostics import MatchDiagnostics  # noqa: E402
from template_catalog import TemplateCatalog  # noqa: E402

START_ALL_BOX = (50, 75, 90, 110)
WINDOW_BOX = (15, 20, 125, 140)
DOCK_BOX = (185, 15, 225, 55)
FOUR = [(40, 50), (120, 50), (40, 90), (120, 90)]


def _screen(seed=12):
    return np.random.RandomState(seed).randint(0, 255, (180, 260, 3), dtype=np.uint8)


def _covered(screen, fraction=0.5):
    """The screen with a Dock tooltip over Start All (``fraction`` of its height)."""
    image = screen.copy()
    x0, y0, x1, y1 = START_ALL_BOX
    image[y0:y0 + int((y1 - y0) * fraction), x0:x1] = 235
    return image


def _bt_templates(root, screen):
    catalog = TemplateCatalog(root)
    catalog.save_crop(screen, WINDOW_BOX, "BT", "window")
    catalog.save_crop(screen, DOCK_BOX, "BT", "dock_icon")
    catalog.save_crop(screen, START_ALL_BOX, "BT", "start_all")
    return catalog


class PhaseKvm(FakeKVM):
    """Serves ``images[min(moves, len - 1)]``: the picture changes after each pointer move."""

    def __init__(self, images):
        super().__init__(images[0])
        self.images = images
        self.events = []
        self.rpc_errors = []
        self._seq = 0

    @property
    def moves(self):
        return [e[1:] for e in self.events if e[0] == "move"]

    @property
    def clicks(self):
        return [e[1:] for e in self.events if e[0] == "click"]

    async def click(self, x, y):
        self.events.append(("click", x, y))

    async def move(self, x, y):
        self.events.append(("move", x, y))

    def latest_frame(self):
        self.events.append(("frame",))
        self._seq += 1
        image = self.images[min(len(self.moves), len(self.images) - 1)]
        return make_snapshot(image.copy(), sequence=self._seq, received_monotonic=time.monotonic())


class StreamKvm(FakeKVM):
    """Frames from before the move until ``arrive_after`` s later, then new ones at ``fps``."""

    def __init__(self, stale, fresh, arrive_after=0.15, fps=20):
        super().__init__(stale)
        self.stale, self.fresh, self.arrive_after, self.fps = stale, fresh, arrive_after, fps
        self.moved_at = None
        self.moves = []
        self.born = time.monotonic()
        self.rpc_errors = []

    async def move(self, x, y):
        self.moves.append((x, y))
        self.moved_at = time.monotonic()

    def arrived(self):
        if self.moved_at is None:
            return 0
        t = time.monotonic() - self.moved_at - self.arrive_after
        return 0 if t < 0 else int(t * self.fps) + 1

    def latest_frame(self):
        n = self.arrived()
        if n == 0:
            return make_snapshot(self.stale.copy(), sequence=1, received_monotonic=self.born)
        received = self.moved_at + self.arrive_after + (n - 1) / self.fps
        return make_snapshot(self.fresh.copy(), sequence=1 + n, received_monotonic=received)


def _session(kvm, root):
    return HidSession(kvm, root, MatchDiagnostics(TemplateCatalog(root).root, "BT"), log=lambda *_: None)


# --- fresh frames ------------------------------------------------------------

def test_wait_returns_frames_received_after_the_park(tmp_path, monkeypatch):
    monkeypatch.setattr(hid_session, "FRESH_FRAME_TIMEOUT_SEC", 2.0)
    stale, fresh = np.zeros((40, 80, 3), np.uint8), np.full((40, 80, 3), 90, np.uint8)
    kvm = StreamKvm(stale, fresh)
    session = _session(kvm, tmp_path)

    async def go():
        await session.park_pointer(stale)
        started = time.monotonic()
        image = await session.wait_fresh_frame()
        return image, time.monotonic() - started

    image, waited = asyncio.run(go())
    assert np.array_equal(image, fresh)
    assert waited >= kvm.arrive_after
    assert kvm.latest_frame().sequence >= 3  # at least two frames arrived after the park


def test_wait_times_out_and_continues_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(hid_session, "FRESH_FRAME_TIMEOUT_SEC", 0.2)
    stale = np.zeros((40, 80, 3), np.uint8)
    kvm = StreamKvm(stale, stale, arrive_after=60)
    session = _session(kvm, tmp_path)

    async def go():
        await session.park_pointer(stale)
        return await session.wait_fresh_frame()

    assert np.array_equal(asyncio.run(go()), stale)
    keys = [record["key"] for record in session.diagnostics.records]
    assert "fresh_frame_timeout" in keys


def test_timeout_comes_from_the_settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hid_session, "FRESH_FRAME_TIMEOUT_SEC", 30.0)
    (tmp_path / "COMMON").mkdir()
    (tmp_path / "COMMON" / "hid_settings.json").write_text('{"fresh_frame_timeout_sec": 0.1}')
    stale = np.zeros((40, 80, 3), np.uint8)
    session = _session(StreamKvm(stale, stale, arrive_after=60), tmp_path)

    async def go():
        await session.park_pointer(stale)
        started = time.monotonic()
        await session.wait_fresh_frame()
        return time.monotonic() - started

    assert asyncio.run(go()) < 5


def test_kvm_without_frame_sequence_falls_back_to_a_fixed_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(hid_session, "FRESH_FALLBACK_SEC", 0.05)
    image = np.zeros((40, 80, 3), np.uint8)

    class Bare:
        def latest_frame(self):
            return type("Snapshot", (), {"image": image})()

    session = _session(Bare(), tmp_path)

    async def go():
        await session.park_pointer(image)
        return await session.wait_fresh_frame()

    assert asyncio.run(go()) is image
    assert [r["key"] for r in session.diagnostics.records] == []


# --- park position -----------------------------------------------------------

def test_default_park_is_top_edge_middle(tmp_path):
    kvm = PhaseKvm([_screen()])
    asyncio.run(_session(kvm, tmp_path).park_pointer(_screen()))
    assert kvm.moves == [(130, 1)]


def test_park_position_can_be_overridden(tmp_path):
    (tmp_path / "COMMON").mkdir()
    (tmp_path / "COMMON" / "hid_settings.json").write_text('{"pointer_park": [100, 170]}')
    kvm = PhaseKvm([_screen()])
    asyncio.run(_session(kvm, tmp_path).park_pointer(_screen()))
    assert kvm.moves == [(100, 170)]


def test_park_position_is_clamped_to_the_frame(tmp_path):
    (tmp_path / "COMMON").mkdir()
    (tmp_path / "COMMON" / "hid_settings.json").write_text('{"pointer_park": [5000, 5000]}')
    kvm = PhaseKvm([_screen()])
    asyncio.run(_session(kvm, tmp_path).park_pointer(_screen()))
    assert kvm.moves == [(259, 179)]


@pytest.mark.parametrize("text", ['{"pointer_park": [1]}', '{"pointer_park": "top"}', "not json",
                                  '{"fresh_frame_timeout_sec": 0}'])
def test_invalid_settings_are_hid_failed(tmp_path, text):
    (tmp_path / "COMMON").mkdir()
    (tmp_path / "COMMON" / "hid_settings.json").write_text(text)
    kvm = PhaseKvm([_screen()])
    with pytest.raises(FlowError) as excinfo:
        asyncio.run(_session(kvm, tmp_path).park_pointer(_screen()))
    assert excinfo.value.code == "hid_failed"
    assert kvm.moves == []


# --- BT ----------------------------------------------------------------------

def _run_bt(kvm, root):
    return asyncio.run(bt_flow.run_start_all(kvm, template_root=root, log=lambda *_: None))


def test_bt_parks_after_the_dock_click_and_before_matching(tmp_path):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    kvm = PhaseKvm([screen])

    assert _run_bt(kvm, tmp_path) == {"action": "start_all"}

    kinds = [e[0] for e in kvm.events]
    dock_click = kinds.index("click")
    first_move = kinds.index("move")
    assert dock_click < first_move
    assert "frame" in kinds[first_move:]  # the frame used for matching is read after the park
    assert kinds[dock_click + 1] == "move"
    assert kvm.moves[0] == (130, 1)
    assert len(kvm.clicks) == 2
    assert [k for k in kinds if k != "frame"][-1] == "move"  # parked again after Start All


def test_bt_retries_on_fresh_frames_without_a_second_dock_click(tmp_path):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    # 0 moves: clean screen for the Dock; 1: tooltip over Start All; 2+: clean
    kvm = PhaseKvm([screen, _covered(screen), screen])

    assert _run_bt(kvm, tmp_path) == {"action": "start_all"}

    assert len(kvm.clicks) == 2  # Dock once, Start All once
    dock_x, dock_y = kvm.clicks[0]
    assert dock_x > 150
    start_x, start_y = kvm.clicks[1]
    assert 50 <= start_x <= 90 and 75 <= start_y <= 110
    assert len(kvm.moves) == 3  # after Dock, retry park, after Start All
    summary = MatchDiagnostics.load(TemplateCatalog(tmp_path).root, "BT")
    keys = [record["key"] for record in summary["records"]]
    assert "start_all" in keys and "start_all_retry" in keys and "window_retry" in keys
    assert summary["ok"] is True


def test_bt_both_attempts_failing_reports_the_higher_score(tmp_path):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    kvm = PhaseKvm([screen, _covered(screen, 0.5), _covered(screen, 0.2)])

    with pytest.raises(FlowError) as excinfo:
        _run_bt(kvm, tmp_path)

    assert excinfo.value.code == "hid_failed"
    assert len(kvm.clicks) == 1  # only the Dock click
    summary = MatchDiagnostics.load(TemplateCatalog(tmp_path).root, "BT")
    scores = {record["key"]: record["score"] for record in summary["records"]
              if record["key"].startswith("start_all") and record["score"] is not None}
    first = max(v for k, v in scores.items() if not k.endswith("_retry"))
    second = max(v for k, v in scores.items() if k.endswith("_retry"))
    assert first != second
    assert f"similarity {max(first, second):.2f}" in str(excinfo.value)
    assert summary["ok"] is False


# --- DFU ---------------------------------------------------------------------

class DfuPhaseKvm(SyntheticDfuKVM):
    """Moves are recorded. With ``hide_phase`` set, the checkboxes and OK button are missing after that many moves."""

    def __init__(self, *args, hide_phase=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hide_phase = hide_phase

    async def move(self, x, y):
        self.events.append(("move", x, y))

    def latest_frame(self):
        snapshot = super().latest_frame()
        moves = sum(1 for e in self.events if e[0] == "move")
        if self.hide_phase is not None and moves == self.hide_phase:
            image = snapshot.image
            for px, py in self.positions:
                image[py:py + 14, px:px + 14] = 200
            bx, by, bw, bh = BUTTON
            image[by:by + bh, bx:bx + bw] = 200
            snapshot = make_snapshot(image, sequence=snapshot.sequence,
                                     received_monotonic=snapshot.received_monotonic)
        return snapshot


def _dfu_events(kvm):
    return [e for e in kvm.events if e[0] != "move"]


def test_dfu_input_retry_does_not_repeat_clicks_or_typing(tmp_path):
    root = tmp_path / "template"
    save_templates(root, FOUR)
    items = [{"slot": 1, "sn": "AAA"}, {"slot": 3, "sn": "CCC"}]

    baseline = DfuPhaseKvm(FOUR)
    asyncio.run(dfu_flow.run_dfu_input(baseline, 4, items, root, log=lambda *_: None))
    retried = DfuPhaseKvm(FOUR, hide_phase=1)
    result = asyncio.run(dfu_flow.run_dfu_input(retried, 4, items, root, log=lambda *_: None))

    assert result == {"entered": [1, 3]}
    assert _dfu_events(retried) == _dfu_events(baseline)  # same clicks, same typing, once
    assert sum(1 for e in retried.events if e[0] == "type") == 2
    assert retried.events[1][0] == "move"  # parked right after the Dock click
    keys = [r["key"] for r in MatchDiagnostics.load(TemplateCatalog(root).root, "DFU")["records"]]
    assert "checkboxes_retry" in keys


def test_dfu_ok_retry_clicks_dock_and_ok_once(tmp_path):
    root = tmp_path / "template"
    save_templates(root, FOUR)
    kvm = DfuPhaseKvm(FOUR, hide_phase=1)

    assert asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=lambda *_: None)) == {"action": "ok"}

    assert len([e for e in kvm.events if e[0] == "click"]) == 2
    assert kvm.events[-1][0] == "move"  # parked after OK
    keys = [r["key"] for r in MatchDiagnostics.load(TemplateCatalog(root).root, "DFU")["records"]]
    assert "button_retry" in keys


def test_dfu_ok_failing_twice_clicks_only_the_dock(tmp_path):
    root = tmp_path / "template"
    save_templates(root, FOUR)

    class AlwaysHidden(DfuPhaseKvm):
        def latest_frame(self):
            self.hide_phase = sum(1 for e in self.events if e[0] == "move")
            return super().latest_frame() if self.hide_phase else SyntheticDfuKVM.latest_frame(self)

    kvm = AlwaysHidden(FOUR)
    with pytest.raises(FlowError) as excinfo:
        asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=lambda *_: None))
    assert excinfo.value.code == "hid_failed"
    assert len([e for e in kvm.events if e[0] == "click"]) == 1


# --- monitor -----------------------------------------------------------------

def test_monitor_dock_click_parks_and_waits_for_fresh_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(hid_session, "FRESH_FRAME_TIMEOUT_SEC", 2.0)
    screen = _screen()
    TemplateCatalog(tmp_path).save_crop(screen, DOCK_BOX, "COMMON", "log_solution_dock_icon")
    kvm = StreamKvm(screen, _covered(screen))
    kvm.clicks = []

    async def click(x, y):
        kvm.clicks.append((x, y))

    kvm.click = click
    session = _session(kvm, tmp_path)

    clicked, _score = asyncio.run(monitor_flow.click_dock_icon(kvm, screen, session))

    assert clicked and len(kvm.clicks) == 1
    assert kvm.moves == [(130, 1)]
    assert kvm.latest_frame().sequence >= 3  # returned only after post-park frames arrived


# --- review round 1 ------------------------------------------------------------

def test_frames_that_arrived_during_the_settle_count_as_fresh(tmp_path):
    old, fresh = np.zeros((40, 80, 3), np.uint8), np.full((40, 80, 3), 90, np.uint8)
    kvm = FakeKVM(old, sequence=10, received_monotonic=time.monotonic() - 1)

    async def move(x, y):
        pass

    kvm.move = move
    session = _session(kvm, tmp_path)

    async def go():
        mark = await session.park_pointer(old)
        # 11 and 12 came in during the settle (explicit time: the Windows clock ticks in ~15 ms steps)
        kvm.frame, kvm.frame_sequence, kvm.frame_received_monotonic = fresh, 12, mark + 0.05
        return await session.wait_fresh_frame()

    assert np.array_equal(asyncio.run(go()), fresh)
    assert "fresh_frame_timeout" not in [r["key"] for r in session.diagnostics.records]


class RpcErrorKvm(PhaseKvm):
    """HID RPC errors: ``click_error`` on every click, ``move_error`` on every move."""

    click_error = False
    move_error = False

    async def click(self, x, y):
        await super().click(x, y)
        if self.click_error:
            self.rpc_errors.append("click rejected")

    async def move(self, x, y):
        await super().move(x, y)
        if self.move_error:
            self.rpc_errors.append("park only rejected")


def test_bt_park_error_after_a_successful_click_is_not_a_failure(tmp_path):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    kvm = RpcErrorKvm([screen])
    kvm.move_error = True

    assert _run_bt(kvm, tmp_path) == {"action": "start_all"}

    assert len(kvm.clicks) == 2  # Dock and Start All each once
    summary = MatchDiagnostics.load(TemplateCatalog(tmp_path).root, "BT")
    assert "park_failed" in [r["key"] for r in summary["records"]]


def test_bt_click_error_is_still_hid_failed(tmp_path):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    kvm = RpcErrorKvm([screen])
    kvm.click_error = True

    with pytest.raises(FlowError) as excinfo:
        _run_bt(kvm, tmp_path)

    assert excinfo.value.code == "hid_failed"
    assert len(kvm.clicks) == 1


def test_dfu_ok_park_error_after_a_successful_click_is_not_a_failure(tmp_path):
    root = tmp_path / "template"
    save_templates(root, FOUR)

    class Kvm(DfuPhaseKvm):
        async def move(self, x, y):
            await super().move(x, y)
            self.rpc_errors.append("park only rejected")

    kvm = Kvm(FOUR)
    assert asyncio.run(dfu_flow.run_dfu_ok(kvm, root, log=lambda *_: None)) == {"action": "ok"}
    assert len([e for e in kvm.events if e[0] == "click"]) == 2


@pytest.mark.parametrize("text", ['{"fresh_frame_timeout_sec": null}', '{"pointer_park": null}',
                                  '{"pointer_park": "bad"}', '{"fresh_frame_timeout_sec": "2"}'])
def test_invalid_settings_fail_before_the_first_click(tmp_path, text):
    screen = _screen()
    _bt_templates(tmp_path, screen)
    (tmp_path / "COMMON").mkdir(exist_ok=True)
    (tmp_path / "COMMON" / "hid_settings.json").write_text(text)
    kvm = PhaseKvm([screen])
    with pytest.raises(FlowError) as excinfo:
        _run_bt(kvm, tmp_path)
    assert excinfo.value.code == "hid_failed"
    assert kvm.clicks == []

    dfu_root = tmp_path / "dfu"
    save_templates(dfu_root, FOUR)
    (dfu_root / "COMMON").mkdir()
    (dfu_root / "COMMON" / "hid_settings.json").write_text(text)
    dfu = DfuPhaseKvm(FOUR)
    with pytest.raises(FlowError) as excinfo:
        asyncio.run(dfu_flow.run_dfu_ok(dfu, dfu_root, log=lambda *_: None))
    assert excinfo.value.code == "hid_failed"
    assert [e for e in dfu.events if e[0] == "click"] == []


def test_monitor_fresh_frame_wait_is_inside_the_confirm_window(tmp_path, monkeypatch):
    import threading
    from protocol_helpers import LogSolutionKVM

    monkeypatch.setattr(hid_session, "FRESH_FRAME_TIMEOUT_SEC", 0.4)
    seed = LogSolutionKVM()
    x, y, w, h = seed.dock_icon_box()
    TemplateCatalog(tmp_path).save_crop(seed.latest_frame().image[y:y + h, x:x + w].copy(), (0, 0, w, h),
                                        "COMMON", "log_solution_dock_icon")
    kvm = LogSolutionKVM(state="standby")
    kvm.blank()
    kvm.repeat_sequence = True  # no new frame ever arrives: the fresh wait runs to its 0.4 s limit

    class Gate:
        stats = None

    threading.Timer(0.6, lambda: kvm.show("standby")).start()  # reliable, but after the 0.5 s confirm window
    with pytest.raises(FlowError) as excinfo:
        asyncio.run(monitor_flow.run_monitor_start(
            kvm, Gate(), "DFU-01", template_root=tmp_path, confirm_sec=0.5, start_timeout_sec=0.1,
            log=lambda *_: None))

    assert excinfo.value.code == "log_solution_not_found"
    assert [e for e in kvm.events if e[0] == "key"] == []
