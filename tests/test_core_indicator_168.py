"""Core light of the bridge top bar (#168): states, boundaries, shared settings, startup fallback. No Tk, no sockets."""
import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))
import core_daemon as core  # noqa: E402
import core_indicator as ci  # noqa: E402

SETTINGS = {"interval_minutes": 1, "red_threshold_seconds": 120}
WALL0 = datetime.datetime(2026, 11, 1, 8, 0, 0).astimezone()


def make():
    return ci.CoreIndicator(0.0, WALL0, "LOG", SETTINGS)


def wall(seconds):
    return WALL0 + datetime.timedelta(seconds=seconds)


def failed_file(reason, after=5, **extra):
    return {"state": "failed", "reason": reason, "detail": "first error", "devices_file": "DEV", "port": 5000,
            "updated_at": wall(after).isoformat(), **extra}


def show(ind):
    return (ind.state, ind.light, ind.symbol, ind.text_key)


def test_starting_inside_grace_then_waiting_after_it():
    c = make()
    assert show(c.observe(False, None, 29.9, wall(29.9))) == ("starting", "grey", "○", "core_ind_starting")
    assert c.observe(False, None, 30.0, wall(30)).state == "waiting"


@pytest.mark.parametrize("reason,key,path", [
    ("devices_missing", "core_ind_fail_devices_missing", "DEV"),
    ("devices_invalid", "core_ind_fail_devices_invalid", "DEV"),
    ("port_in_use", "core_ind_fail_port_in_use", "LOG"),
])
def test_failed_start_shows_reason_notice_and_path(reason, key, path):
    ind = make().observe(False, None, 6.0, wall(6), failed_file(reason))
    assert show(ind) == ("failed", "red", "×", key)
    assert ind.small_key == "core_ind_check_config" and ind.small_args == {"path": path}
    assert ind.text_args["port"] == 5000 and ind.text_args["detail"] == "first error"


def test_failed_file_from_before_this_launch_is_ignored():
    ind = make().observe(False, None, 6.0, wall(6), failed_file("devices_missing", after=-60))
    assert ind.state == "starting"


def test_invalid_host_is_its_own_red_state():
    ind = make().observe(False, None, 6.0, wall(6), failed_file("invalid_host"))
    assert show(ind) == ("invalid_host", "red", "×", "core_ind_invalid_host")
    assert (ind.small_key, ind.small_args) == ("core_ind_invalid_host_small", {"path": "LOG"})


def test_down_after_connected_is_waiting_until_the_threshold_then_red():
    c = make()
    c.observe(True, 500, 100.0, wall(100))
    assert show(c.observe(False, None, 220.0, wall(220))) == ("waiting", "orange", "△", "core_ind_waiting")
    over = c.observe(False, None, 220.1, wall(220.1))
    assert show(over) == ("stopped_long", "red", "×", "core_ind_stopped_long")
    assert over.text_args == {"minutes": 2} and over.small_args == {"path": "LOG"}


def test_user_restart_shows_starting_not_waiting():
    c = make()
    c.observe(True, 500, 100.0, wall(100))
    c.mark_launch(200.0, wall(200))
    assert c.observe(False, None, 229.9, wall(229.9)).state == "starting"
    assert c.observe(False, None, 230.0, wall(230)).state == "waiting"


def test_recovery_after_waiting_shows_restart_time_in_local_time():
    c = make()
    c.observe(True, 500, 100.0, wall(100))
    c.observe(False, None, 160.0, wall(160))
    ok = c.observe(True, 3, 170.0, wall(170))  # 08:02:50
    assert show(ok) == ("ok", "green", "√", "core_ind_ok")
    assert (ok.small_key, ok.small_args) == ("core_ind_restarted", {"time": "08:02"})


def test_smaller_uptime_means_core_was_replaced():
    c = make()
    c.observe(True, 500, 100.0, wall(100))
    ok = c.observe(True, 2, 101.5, wall(101.5))
    assert ok.small_key == "core_ind_restarted"


def test_plain_ok_has_no_restart_note():
    c = make()
    assert c.observe(True, 500, 1.0, wall(1)).small_key is None
    assert c.observe(True, 502, 3.0, wall(3)).small_key is None


def test_indicator_reads_the_scheduler_settings_file(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"interval_minutes": 2, "red_threshold_seconds": 300}))
    c = ci.CoreIndicator(0.0, WALL0, "LOG", ci.load_scheduler_settings(f))
    c.observe(True, 5, 0.0, WALL0)
    assert c.observe(False, None, 300.0, wall(300)).text_args == {"minutes": 2}  # waiting, "about 2 min"
    assert c.observe(False, None, 300.1, wall(300.1)).state == "stopped_long"


def test_settings_defaults_when_file_is_missing_or_broken(tmp_path):
    assert ci.load_scheduler_settings(tmp_path / "none.json") == SETTINGS
    (tmp_path / "bad.json").write_text("{")
    assert ci.load_scheduler_settings(tmp_path / "bad.json") == SETTINGS


def test_install_script_and_indicator_share_one_settings_file():
    shipped = json.loads((ROOT / "windows" / "core-scheduler.json").read_text())
    assert ci.load_scheduler_settings(ci.scheduler_settings_path()) == shipped == SETTINGS
    script = (ROOT / "windows" / "install-core.ps1").read_text()
    assert "core-scheduler.json" in script and "interval_minutes" in script and "red_threshold_seconds" in script
    assert "RestartCount" not in script


def test_fallback_does_not_start_core_when_ping_answers():
    calls = []
    out = core.ensure_core_running(ping=lambda: {"data": {"pong": True}}, start=lambda **kw: calls.append(kw))
    assert out == "running" and calls == []


def test_fallback_starts_core_when_nothing_answers():
    def no_answer():
        raise OSError("refused")
    calls = []
    out = core.ensure_core_running(ping=no_answer, start=lambda **kw: calls.append(kw) or {"status": "ok"})
    assert out == "started" and len(calls) == 1


def test_scheduler_started_core_at_the_same_moment_counts_as_running():
    out = core.ensure_core_running(ping=lambda: {}, start=lambda **kw: {"status": "already_running"})
    assert out == "running"


def test_fallback_reports_failure_instead_of_raising():
    def boom(**kw):
        raise RuntimeError("port in use")
    assert core.ensure_core_running(ping=lambda: {}, start=boom) == "failed"
    assert core.ensure_core_running(ping=lambda: {}, start=lambda **kw: {"status": "error"}) == "failed"


def test_port_taken_by_a_core_that_answers_ping_is_normal(monkeypatch):
    monkeypatch.setattr(core, "send_tcp_command", lambda *a, **k: {"status": "ok", "data": {"pong": True}})
    monkeypatch.setattr(core, "is_port_free", lambda *a, **k: False)
    res = core.start_core_daemon(port=5000)
    assert res["status"] == "already_running"


def test_core_wins_race_between_initial_ping_and_port_check(monkeypatch):
    responses = iter([{}, {"status": "ok", "data": {"pong": True}}])
    monkeypatch.setattr(core, "send_tcp_command", lambda *a, **k: next(responses))
    monkeypatch.setattr(core, "is_port_free", lambda *a, **k: False)
    assert core.start_core_daemon()["status"] == "already_running"


def test_manual_restart_is_not_reported_as_automatic_restart():
    c = make()
    c.observe(True, 500, 100.0, wall(100))
    c.mark_launch(101.0, wall(101))
    assert c.observe(True, 2, 103.0, wall(103)).small_key is None


def test_settings_file_with_utf8_bom_is_read(tmp_path):
    f = tmp_path / "s.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps({"interval_minutes": 3, "red_threshold_seconds": 400}).encode())
    assert ci.load_scheduler_settings(f) == {"interval_minutes": 3, "red_threshold_seconds": 400}
