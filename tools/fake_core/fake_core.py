"""模擬 Core（fake core）：讓 LabVIEW 開發者沒有 KVM、BT 實機也能測 Core Client VI。

走真的協定（docs/PROTOCOL.md，TCP、一行一個 JSON、LF 結尾），驗證順序、錯誤碼、message、data 預設形狀
都照 host-app/tcp_server.py。只用 Python 標準函式庫。畫面／輪次狀態轉換是 Device 上的公開方法，
並提供 FakeCore.add_listener() 事件掛鉤，方便之後接「對方模擬器」。
"""

import argparse
import copy
import json
import select
import socket
import socketserver
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = "1.0"
CORE_VERSION = "2.0.0-fake"
COMMON_FIELDS = frozenset({"protocol_version", "req_id", "device", "cmd"})
CHECK_MAX_TIMEOUT_SEC = 3600
MONITOR_START_MAX_TIMEOUT_SEC = 300
FRAME_GAP_SEC = 1.0  # 兩張完成畫面的間隔上限（DeviceRound.max_frame_gap）
CONNECT_FAIL_COUNT = 3
RESULT_STATUSES = ("PASS", "FAIL", "NOTEST", "TIMEOUT", "STOPPED")
NOT_FOUND_REASON = "locators_missing_or_ambiguous"
KVM_NAME = "fake"
SUPPORTED_STATIONS = ("BT", "DFU", "FCT")  # FCT 沒有治具訊號，monitor_start 成功即視為治具已推入


def _unknown(reason: str = "") -> Dict[str, Any]:
    return {"kind": "unknown", "state": "", "reason": reason, "capacity": 0, "active": [], "results": []}


class ArgError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _timeout_arg(req: Dict[str, Any], low: int, high: int) -> float:
    timeout = req.get("timeout_sec")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ArgError("invalid_argument", "timeout_sec is required and must be a number")
    if not low <= timeout <= high:
        raise ArgError("invalid_argument", f"timeout_sec must be between {low} and {high}")
    return float(timeout)


def _check_timeout(req: Dict[str, Any]) -> float:
    return _timeout_arg(req, 0, CHECK_MAX_TIMEOUT_SEC)


def _monitor_timeout(req: Dict[str, Any]) -> float:
    return _timeout_arg(req, 1, MONITOR_START_MAX_TIMEOUT_SEC)


def _snapshot_args(req: Dict[str, Any]) -> Any:
    fmt = req.get("format")
    if fmt not in ("jpeg", "png"):
        raise ArgError("invalid_argument", "format must be 'jpeg' or 'png'")
    quality = req.get("quality")
    if isinstance(quality, bool) or not isinstance(quality, int):
        raise ArgError("invalid_argument", "quality is required and must be an integer")
    if fmt == "png" and quality != 0:
        raise ArgError("invalid_argument", "quality must be 0 for png")
    if fmt == "jpeg" and not 1 <= quality <= 100:
        raise ArgError("invalid_argument", "quality must be between 1 and 100 for jpeg")
    return fmt, quality


@dataclass(frozen=True)
class Spec:
    """tcp_server.CommandSpec 的縮小版：驗證規則與 default_data。handler 空字串 = 不模擬。"""
    handler: str = ""
    fields: frozenset = frozenset()
    device_bound: bool = False
    takes_lock: bool = False
    stations: Optional[frozenset] = None
    parse: Callable[[Dict[str, Any]], Any] = lambda req: None
    default_data: Dict[str, Any] = field(default_factory=dict)
    unsupported_message: str = ""


COMMANDS: Dict[str, Spec] = {
    "ping": Spec("_ping", default_data={"pong": False, "core_version": ""}),
    "status": Spec("_status", default_data={"core_uptime_sec": 0, "devices": []}),
    "core_status": Spec(),
    "shutdown": Spec(),
    "snapshot": Spec(fields=frozenset({"format", "quality"}), device_bound=True, parse=_snapshot_args,
                     default_data={"width": 0, "height": 0, "format": "", "image_base64": ""}),
    "check_start": Spec("_check_start", fields=frozenset({"timeout_sec"}), device_bound=True, takes_lock=True,
                        parse=_check_timeout, default_data=_unknown()),
    "check_finish": Spec("_check_finish", fields=frozenset({"timeout_sec"}), device_bound=True, takes_lock=True,
                         parse=_check_timeout, default_data=_unknown()),
    "inspect": Spec(fields=frozenset({"timeout_sec"}), device_bound=True, parse=_check_timeout,
                    default_data=_unknown()),
    "input": Spec(fields=frozenset({"items"}), device_bound=True, stations=frozenset({"DFU"}),
                  default_data={"entered": []},
                  unsupported_message="'input' is only supported for DFU devices"),
    "start_test": Spec("_start_test", device_bound=True, takes_lock=True, stations=frozenset({"DFU", "BT"}),
                       default_data={"action": ""},
                       unsupported_message="'start_test' is not supported for FCT; "
                                           "FCT starts when the fixture is pushed in"),
    "monitor_start": Spec("_monitor_start", fields=frozenset({"timeout_sec"}), device_bound=True, takes_lock=True,
                          parse=_monitor_timeout, default_data={"state": "", "capacity": 0}),
}


# 情境（scenario）：改變「下一次 start_test 起」發生的事。名稱 → 別名；cs_started = 正常（等於清除情境）。
SCENARIOS = {"cs_started": "css", "cs_taken": "cstk", "cs_timeout": "csto", "cs_paused": "csp",
             "start_test_drop_not_started": "stdrop", "start_test_drop": "stds"}
SCENARIO_ALIASES = {alias: name for name, alias in SCENARIOS.items()}


def resolve_scenario(name: str) -> Optional[str]:
    """名稱或別名 → 正式名稱；None = 不是情境。"""
    name = name.strip().lower()
    return name if name in SCENARIOS else SCENARIO_ALIASES.get(name)


def parse_results(text: str, channels: int) -> List[str]:
    """'PASS' 或逐通道清單 'PASS,FAIL,...'（不足的通道沿用最後一格）→ 每通道一個狀態。"""
    items = [t.strip().upper() for t in text.split(",") if t.strip()]
    if not items or any(i not in RESULT_STATUSES for i in items):
        raise ValueError(f"result must be PASS, FAIL, TIMEOUT or a comma list of {'/'.join(RESULT_STATUSES)}")
    return [items[i] if i < len(items) else items[-1] for i in range(channels)]


class Device:
    """一台設備的畫面／輪次狀態。畫面 screen ∈ standby / monitoring / review / complete。

    公開方法就是「畫面轉換」，之後的對方模擬器可直接呼叫：
    enter_review()、confirm(to)、monitor_ready()、test_started()、reset()。
    """

    def __init__(self, core: "FakeCore", device_id: str, station: str, channels: int) -> None:
        self.core = core
        self.device_id = device_id
        self.station = station
        self.channels = channels
        self.lock = threading.Lock()  # 上鎖指令（monitor_start / start_test / check_finish）
        self.reset()

    def reset(self) -> None:
        self.screen = "standby"
        self.start_requested = False
        self.armed = False
        self.delivered: Optional[tuple] = None  # 上一次交付的結果（DeviceRound.delivered_signature）
        self.candidate_at: Optional[float] = None  # 兩張完成畫面規則：上一張完成畫面的時間
        self.running = False
        self.finish_at = 0.0
        self.visible = False  # 畫面上看得到通道在測試（check_start 的活動證據）
        self.outcome = "complete"  # finish_at 到時出現的畫面：complete 或 review
        self.activity_prev: Optional[float] = None  # 活動配對：上一張看得到測試的影格時間

    def begin_start(self) -> None:
        """monitor_start 一開始：放掉上次交付與 armed；start_requested 保持原樣。"""
        self.armed = False
        self.delivered = None
        self.candidate_at = None

    def monitor_ready(self) -> None:
        """monitor_start 成功：看到監控中並建立本輪開始請求。"""
        self.begin_start()
        self.screen = "monitoring"
        self.start_requested = True
        self.armed = True
        self.running = self.visible = False
        self.activity_prev = None

    def test_started(self) -> None:
        """start_test 成功：預設 finish_delay 秒後出現結果畫面，期間看得到通道在測試。依情境改變。"""
        scenario = self.core.scenario
        self.running = self.visible = False
        self.activity_prev = None
        if scenario == "cs_taken":  # 看不到測試，結果畫面立刻出現
            self.show_result()
        elif scenario in ("cs_timeout", "start_test_drop_not_started"):  # 看不到測試，也不會有結果畫面
            pass
        else:
            self.running = True
            self.visible = scenario != "cs_paused"
            self.outcome = "review" if scenario == "cs_paused" else "complete"
            self.finish_at = time.monotonic() + self.core.finish_delay

    def enter_review(self) -> None:
        self.screen = "review"
        self.running = self.visible = False
        self.candidate_at = self.activity_prev = None

    def show_result(self) -> None:
        self.screen = "complete"
        self.running = self.visible = False
        self.candidate_at = self.activity_prev = None

    def observe_activity(self, now: float) -> bool:
        """一次讀取：連續兩張 ≤1 秒、都看得到測試才算活動（check_start / check_finish 的讀取共用）。"""
        testing = self.running and self.visible and self.screen == "monitoring"
        confirmed = testing and self.activity_prev is not None and now - self.activity_prev <= FRAME_GAP_SEC
        self.activity_prev = now if testing else None
        return confirmed

    def confirm(self, to: str = "complete") -> None:
        """待確認 → 完成（帶設定的結果）或待機。不在待確認時不動作。"""
        if self.screen != "review":
            return
        if to == "standby":
            self.screen = "standby"
        else:
            self.show_result()
        self.candidate_at = None

    def tick(self) -> None:
        """時間到且沒有 timeout 故障，結果畫面出現。"""
        if (self.running and self.screen == "monitoring" and time.monotonic() >= self.finish_at
                and "timeout" not in self.core.faults):
            if self.outcome == "review":
                self.enter_review()
            else:
                self.show_result()
            self.core.emit("result_screen", self.device_id, screen=self.screen)

    def reading(self) -> Dict[str, Any]:
        """畫面現在看到的樣子（waiting 形狀）。"""
        base = {"capacity": self.channels, "active": [], "results": []}
        if self.screen == "review":
            return {"kind": "paused", "state": "review", "reason": "confirmation_required", **base}
        if self.screen == "complete":
            # device_round.judge：未 armed 或沒有開始請求的完成畫面一律是 deliverable_without_monitoring
            reason = ("confirming_complete_frame" if self.armed and self.start_requested
                      else "deliverable_without_monitoring")
            return {"kind": "waiting", "state": "complete", "reason": reason, **base}
        return {"kind": "waiting", "state": self.screen, "reason": self.screen, **base}


class _Conn:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.closed = False

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def peer_gone(self) -> bool:
        if self.closed:
            return True
        try:
            readable, _, _ = select.select([self.sock], [], [], 0)
            return bool(readable) and self.sock.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        core: FakeCore = self.server.core  # type: ignore[attr-defined]
        conn = _Conn(self.request)
        core.add_conn(conn)
        try:
            for line in self.rfile:
                if conn.closed:
                    break
                out = core.process(line, conn)
                if out is not None and not conn.closed:
                    conn.sock.sendall(out)
        except OSError:
            pass
        finally:
            core.remove_conn(conn)


class FakeCore:
    HELP = (
        "disconnect (dc)      立即關閉所有 client 連線，進行中的請求不回覆\n"
        "timeout (to)         [持續] check_finish 不交付，等滿 timeout_sec 回 timeout\n"
        "heartbeat (hb)       [持續] 心跳停止：status frozen=true、connected=false，設備指令 kvm_connect_failed\n"
        "connect_failed (cf)  [持續] status connect_failed=true，設備指令 kvm_connect_failed\n"
        "review (rv)          畫面進入待確認\n"
        "confirm (cn)         待確認 → 完成（confirm standby：待確認 → 待機）\n"
        "shutdown (sd)        [持續] server_shutting_down，並把輪次重設為待機\n"
        "not_found (nf)       [持續] log_solution_not_found\n"
        "clear (c)            清除所有持續故障與情境\n"
        "reset (r)            清除故障與情境並把輪次重設為待機\n"
        "scenario (sc) NAME   選情境（也可直接輸入名稱或別名），下一次 start_test 起生效、持續到 clear：\n"
        "  cs_started (css)   正常：check_start started、check_finish taken（等於清除情境）\n"
        "  cs_taken (cstk)    start_test 後結果畫面立刻出現，check_start 直接 taken\n"
        "  cs_timeout (csto)  看不到測試開始也沒有結果，check_start／check_finish timeout\n"
        "  cs_paused (csp)    finish-delay 秒後進入待確認，check_start paused\n"
        "  start_test_drop_not_started (stdrop)  start_test 不回覆就斷線，測試未開始\n"
        "  start_test_drop (stds)  start_test 不回覆就斷線，但測試照常進行，重連後 check_start started\n"
        "result (res) X       改結果：PASS / FAIL / TIMEOUT 或逐通道清單 PASS,FAIL,...\n"
        "station X            站別執行中不支援切換（FCT 也一樣），請用 --station 啟動\n"
        "state (s)            顯示目前畫面、旗標與故障\n"
        "help (h)             本說明\n"
        "quit (q)             結束"
    )
    FAULTS = {"timeout": "timeout", "to": "timeout", "heartbeat": "heartbeat", "hb": "heartbeat",
              "connect_failed": "connect_failed", "cf": "connect_failed", "shutdown": "shutdown",
              "sd": "shutdown", "not_found": "not_found", "nf": "not_found"}

    def __init__(self, host: str = "0.0.0.0", port: int = 5000, device: str = "BT-01", station: str = "BT",
                 channels: int = 4, finish_delay: float = 5.0, monitor_delay: float = 0.5,
                 result: str = "PASS", not_found_delay: float = 30.0, poll: float = 0.5,
                 log: Optional[Callable[[str], None]] = None, scenario: Optional[str] = None) -> None:
        self.host, self.port = host, port
        canonical = resolve_scenario(scenario) if scenario else None
        if scenario and canonical is None:
            raise ValueError(f"unknown scenario '{scenario}'")
        self.scenario = None if canonical == "cs_started" else canonical
        self.finish_delay, self.monitor_delay = float(finish_delay), float(monitor_delay)
        self.not_found_delay, self.poll = float(not_found_delay), float(poll)
        self.channels = channels
        self.results = parse_results(result, channels)
        self.log = log or (lambda text: print(text, flush=True))
        self.devices = {device: Device(self, device, station, channels)}
        self.faults: set = set()
        self.quit_event = threading.Event()
        self._mu = threading.RLock()
        self._conns: set = set()
        self._listeners: List[Callable[[str, str, Dict[str, Any]], None]] = []
        self._server: Optional[_Server] = None
        self._started = 0.0

    # ---- 擴充點 ------------------------------------------------------------------------------

    def add_listener(self, callback: Callable[[str, str, Dict[str, Any]], None]) -> None:
        """callback(event_name, device_id, details)。事件：monitor_start、start_test、result_screen、taken、
        review、confirm、fault（device_id 為空字串）。callback 的例外會被吞掉。"""
        self._listeners.append(callback)

    def emit(self, name: str, device_id: str = "", **details: Any) -> None:
        for callback in list(self._listeners):
            try:
                callback(name, device_id, details)
            except Exception:
                pass

    # ---- 伺服器生命週期 ------------------------------------------------------------------------

    def start(self) -> int:
        self._server = _Server((self.host, self.port), _Handler)
        self._server.core = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._started = time.time()
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self.port

    def stop(self) -> None:
        self.quit_event.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._close_all_conns()

    def add_conn(self, conn: _Conn) -> None:
        with self._mu:
            self._conns.add(conn)

    def remove_conn(self, conn: _Conn) -> None:
        with self._mu:
            self._conns.discard(conn)

    def _close_all_conns(self) -> int:
        with self._mu:
            conns = list(self._conns)
        for conn in conns:
            conn.close()
        return len(conns)

    @property
    def _stopping(self) -> bool:
        return self.quit_event.is_set()

    # ---- 鍵盤／測試共用的文字指令 ----------------------------------------------------------------

    def command(self, text: str) -> str:
        parts = text.strip().split()
        if not parts:
            return ""
        name, args = parts[0].lower(), [p for p in parts[1:]]
        with self._mu:
            if name in ("help", "h", "?"):
                return self.HELP
            if name in ("disconnect", "dc"):
                n = self._close_all_conns()
                self.emit("fault", "", fault="disconnect")
                return f"已關閉 {n} 個連線"
            if name in self.FAULTS:
                fault = self.FAULTS[name]
                self.faults.add(fault)
                if fault == "shutdown":
                    for dev in self.devices.values():
                        dev.reset()
                self.emit("fault", "", fault=fault, on=True)
                return f"故障 {fault} 已啟用（clear 解除）"
            if name in ("clear", "c"):
                self.faults.clear()
                self.scenario = None
                self.emit("fault", "", fault="clear", on=False)
                return "已清除所有故障與情境"
            if name in ("reset", "r"):
                self.faults.clear()
                self.scenario = None
                for dev in self.devices.values():
                    dev.reset()
                self.emit("fault", "", fault="reset", on=False)
                return "已清除故障並重設為待機"
            if name in ("review", "rv"):
                for dev in self.devices.values():
                    dev.enter_review()
                    self.emit("review", dev.device_id)
                return "畫面進入待確認"
            if name in ("confirm", "cn"):
                to = args[0].lower() if args else "complete"
                if to not in ("complete", "standby"):
                    return "用法：confirm [complete|standby]"
                for dev in self.devices.values():
                    if dev.screen != "review":
                        return f"{dev.device_id} 不在待確認（目前 {dev.screen}）"
                    dev.confirm(to)
                    self.emit("confirm", dev.device_id, to=to)
                return f"已確認 → {to}"
            if name in ("result", "res"):
                if not args:
                    return "目前結果：" + ",".join(self.results)
                try:
                    self.results = parse_results(" ".join(args), self.channels)
                except ValueError as exc:
                    return str(exc)
                return "結果改為 " + ",".join(self.results)
            if name in ("scenario", "sc") or resolve_scenario(name):
                wanted = args[0] if name in ("scenario", "sc") and args else name
                if wanted in ("scenario", "sc"):
                    return "目前情境：" + (self.scenario or "none") + "\n可用：" + self._scenario_names()
                canonical = resolve_scenario(wanted)
                if canonical is None:
                    return f"未知情境 '{wanted}'，可用：" + self._scenario_names()
                self.scenario = None if canonical == "cs_started" else canonical
                self.emit("scenario", "", scenario=canonical)
                return f"情境 {canonical} 已選擇（下一次 start_test 起生效；clear 解除）"
            if name == "station":
                wanted = args[0].upper() if args else ""
                return f"站別執行中不支援切換（FCT 也一樣）；請用 --station 啟動（{'／'.join(SUPPORTED_STATIONS)}）"
            if name in ("state", "s"):
                return self._state_text()
            if name in ("quit", "q"):
                self.quit_event.set()
                return "bye"
        return f"未知指令 '{name}'，輸入 help 看說明"

    @staticmethod
    def _scenario_names() -> str:
        return "、".join(f"{n}({a})" for n, a in SCENARIOS.items())

    def _state_text(self) -> str:
        lines = []
        for dev in self.devices.values():
            dev.tick()
            lines.append(f"{dev.device_id}({dev.station}, {dev.channels}ch): screen={dev.screen} "
                         f"start_requested={dev.start_requested} armed={dev.armed} running={dev.running} "
                         f"delivered={'yes' if dev.delivered else 'no'}")
        lines.append("results=" + ",".join(self.results))
        lines.append("faults=" + (",".join(sorted(self.faults)) or "none"))
        lines.append("scenario=" + (self.scenario or "none"))
        return "\n".join(lines)

    # ---- 一行請求 → 一行回覆 ----------------------------------------------------------------------

    def process(self, raw_line: bytes, conn: _Conn) -> Optional[bytes]:
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        self.log(f"<- {text[:200]}")
        out = self._process(text, conn)
        if out is not None:
            self.log(f"-> {out.decode('utf-8', errors='replace').strip()[:200]}")
        return out

    def _process(self, text: str, conn: _Conn) -> Optional[bytes]:
        started = time.monotonic()
        try:
            req = json.loads(text)
        except Exception as exc:
            return self._envelope(started, "", "", "", "error", "invalid_json", f"Failed to parse JSON: {exc}", {})
        if not isinstance(req, dict):
            return self._envelope(started, "", "", "", "error", "invalid_request", "Request must be a JSON object", {})
        req_id = req.get("req_id") if isinstance(req.get("req_id"), str) else ""
        device = req.get("device") if isinstance(req.get("device"), str) else ""
        cmd = req.get("cmd") if isinstance(req.get("cmd"), str) else ""

        invalid, spec, dev, args = self._admit(req)
        if invalid is not None:
            return self._envelope(started, req_id, device, cmd, "error", invalid[0], invalid[1], {})
        raw = self._dispatch(spec, dev, args, conn)
        if raw is None:  # client 已斷線：不寫任何東西
            return None
        status = raw.get("status", "ok")
        return self._envelope(started, req_id, device, cmd, status, raw.get("error", ""), raw.get("message", ""),
                              raw.get("data", {}), spec.default_data)

    def _admit(self, req: Dict[str, Any]):
        """tcp_server._admit：(error, message) 或 (None, spec, device, args)。"""
        def bad(code: str, message: str):
            return (code, message), None, None, None

        if req.get("protocol_version") != PROTOCOL_VERSION:
            return bad("unsupported_protocol_version", f"protocol_version must be '{PROTOCOL_VERSION}'")
        req_id = req.get("req_id")
        if req_id is None or req_id == "":
            return bad("missing_req_id", "req_id is required")
        if not isinstance(req_id, str):
            return bad("invalid_request", "req_id must be a string")
        cmd = req.get("cmd")
        if cmd is None or cmd == "":
            return bad("missing_command", "cmd is required")
        if not isinstance(cmd, str):
            return bad("invalid_request", "cmd must be a string")
        device = req.get("device")
        if not isinstance(device, str):
            return bad("invalid_request", "device must be a string")
        spec = COMMANDS.get(cmd)
        if spec is None:
            return bad("unknown_command", f"unknown command '{cmd}'")
        for key in req:
            if key in COMMON_FIELDS or key in spec.fields:
                continue
            if key in ("kvm_ip", "station"):
                return bad("forbidden_field", f"field '{key}' is not allowed; address the device by 'device' ID")
            return bad("unknown_field", f"field '{key}' is not defined for '{cmd}'")
        dev = None
        if not spec.device_bound:
            if device != "":
                return bad("invalid_argument", f"device must be empty for '{cmd}'")
        else:
            if device == "":
                return bad("missing_device", f"'{cmd}' requires a 'device' ID")
            dev = self.devices.get(device)
            if dev is None:
                return bad("unknown_device", f"device '{device}' is not configured")
            if spec.stations is not None and dev.station not in spec.stations:
                return bad("unsupported_command", spec.unsupported_message)
        try:
            args = spec.parse(req)
        except ArgError as exc:
            return bad(exc.code, str(exc))
        return None, spec, dev, args

    def _dispatch(self, spec: Spec, dev: Optional[Device], args: Any, conn: _Conn) -> Optional[Dict[str, Any]]:
        if "shutdown" in self.faults:
            return {"status": "error", "error": "server_shutting_down"}
        if not spec.handler:
            return {"status": "error", "error": "unsupported_command",
                    "message": "the fake core does not simulate this command"}
        if dev is None:
            return {"status": "ok", "data": getattr(self, spec.handler)()}
        if spec.takes_lock and not dev.lock.acquire(blocking=False):
            return {"status": "error", "error": "device_busy"}
        try:
            if spec.takes_lock:
                if "connect_failed" in self.faults:
                    return {"status": "error", "error": "kvm_connect_failed",
                            "message": f"Failed to connect to KVM '{KVM_NAME}': simulated connect failure"}
                if "heartbeat" in self.faults:
                    return self._kvm_lost()
            return getattr(self, spec.handler)(dev, args, conn)
        finally:
            if spec.takes_lock:
                dev.lock.release()

    @staticmethod
    def _kvm_lost() -> Dict[str, Any]:
        return {"status": "error", "error": "kvm_connect_failed",
                "message": f"KVM '{KVM_NAME}' connection lost during the command"}

    @staticmethod
    def _envelope(started: float, req_id: str, device: str, cmd: str, status: str, error: str, message: str,
                  payload: Dict[str, Any], default: Optional[Dict[str, Any]] = None) -> bytes:
        spec = COMMANDS.get(cmd)
        base = default if default is not None else (spec.default_data if spec is not None else {})
        envelope = {
            "protocol_version": PROTOCOL_VERSION,
            "req_id": req_id,
            "device": device,
            "cmd": cmd,
            "status": status,
            "error": error if status != "ok" else "",
            "message": message if status != "ok" else "",
            "elapsed_sec": round(time.monotonic() - started, 3),
            "data": {**copy.deepcopy(base), **payload},
        }
        return (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")

    # ---- 指令 ----------------------------------------------------------------------------------

    def _ping(self) -> Dict[str, Any]:
        return {"pong": True, "core_version": CORE_VERSION}

    def _status(self) -> Dict[str, Any]:
        with self._mu:
            heartbeat, failed = "heartbeat" in self.faults, "connect_failed" in self.faults
            devices = [{
                "device": d.device_id, "station": d.station, "connected": not (heartbeat or failed),
                "busy": d.lock.locked(), "frozen": heartbeat, "channels": d.channels,
                "connect_failed": failed, "connect_fail_count": CONNECT_FAIL_COUNT if failed else 0,
            } for d in self.devices.values()]
        return {"core_uptime_sec": int(time.time() - self._started), "devices": devices}

    def _monitor_start(self, dev: Device, timeout: float, conn: _Conn) -> Dict[str, Any]:
        with self._mu:
            dev.begin_start()  # 同真 Core：畫面拒絕之後、任何 log_solution_* 錯誤之前就丟掉上一輪
            dev.tick()
            if "not_found" in self.faults:
                return {"status": "error", "error": "log_solution_not_found",
                        "message": "Log Solution Dock icon is not on screen"}
            if dev.screen == "review":
                return {"status": "error", "error": "log_solution_review",
                        "message": "Log Solution 為待確認（開始前），未繼續開始監控"}
            if dev.screen == "monitoring":
                return {"status": "error", "error": "log_solution_busy",
                        "message": "Log Solution 為 monitoring，未送出快捷鍵"}
        time.sleep(self.monitor_delay)
        with self._mu:
            dev.monitor_ready()
            if dev.station == "FCT":  # FCT 沒有治具訊號：monitor_start 成功即視為治具已推入
                dev.test_started()
        self.emit("monitor_start", dev.device_id)
        return {"status": "ok", "data": {"state": "monitoring", "capacity": dev.channels}}

    def _start_test(self, dev: Device, args: Any, conn: _Conn) -> Dict[str, Any]:
        with self._mu:
            dev.tick()
            unreadable = "not_found" in self.faults
            if unreadable or dev.screen != "monitoring":
                state = "unreadable" if unreadable else dev.screen
                return {"status": "error", "error": "monitor_not_ready",
                        "message": f"Log Solution is not monitoring ({state}); no HID sent"}
            dev.test_started()
        self.emit("start_test", dev.device_id)
        if self.scenario in ("start_test_drop_not_started", "start_test_drop"):
            conn.close()  # 不回覆就關掉連線；鎖由 _dispatch 釋放（start_test_drop 的測試照常進行）
            return None
        return {"status": "ok", "data": {"action": "start_all" if dev.station == "BT" else "ok"}}

    def _fail(self, code: str, message: str, last: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "error", "error": code, "message": message,
                "data": {**last, "kind": "unknown", "results": []}}

    def _step(self, dev: Device, now: float, mode: str):
        """RoundCheck.step 一輪：(回覆, 最後讀值)；回覆為 None = 繼續等。"""
        dev.tick()
        last = dev.reading()
        confirmed = dev.observe_activity(now)
        results = tuple(self.results)
        rows = [{"slot": i, "status": s} for i, s in enumerate(results, 1)]
        if dev.screen == "review":
            return {"status": "ok", "data": last}, last
        if dev.screen == "complete" and dev.delivered == results:
            return {"status": "ok", "data": {**last, "kind": "already_taken", "reason": "round_already_taken",
                                             "results": rows}}, last
        if not dev.start_requested:
            return self._fail("monitor_not_ready", "no round-start request; result was not taken", last), last
        if dev.screen == "complete" and not dev.armed:
            dev.start_requested = False
            return self._fail("manual_verification_required",
                              "start was requested but this round's monitoring was not seen", last), last
        if dev.screen == "complete":
            if dev.candidate_at is not None and now - dev.candidate_at <= FRAME_GAP_SEC:
                dev.start_requested = False
                dev.armed = False
                dev.delivered = results
                dev.candidate_at = None
                self.emit("taken", dev.device_id, results=list(results))
                return {"status": "ok", "data": {**last, "kind": "taken", "reason": "reliable_complete",
                                                 "results": rows}}, last
            dev.candidate_at = now
            return None, last
        if dev.screen == "standby" and dev.armed:
            dev.armed = False
            return {"status": "error", "error": "round_lost",
                    "message": "Log Solution returned to standby before this round was taken",
                    "data": _unknown()}, last
        if mode == "start" and confirmed and dev.screen == "monitoring":
            # 只回報、不結算：start_requested / armed 不變，之後 check_finish 仍可 taken
            active = [{"slot": i, "evidence": "testing"} for i in range(1, dev.channels + 1)]
            return {"status": "ok", "data": {**last, "kind": "started", "reason": "activity_confirmed",
                                             "active": active, "results": []}}, last
        return None, last

    def _check_start(self, dev: Device, timeout: float, conn: _Conn) -> Optional[Dict[str, Any]]:
        return self._check(dev, timeout, conn, "start")

    def _check_finish(self, dev: Device, timeout: float, conn: _Conn) -> Optional[Dict[str, Any]]:
        return self._check(dev, timeout, conn, "finish")

    def _check(self, dev: Device, timeout: float, conn: _Conn, mode: str) -> Optional[Dict[str, Any]]:
        """check_start / check_finish 共用的長輪詢（tcp_server._long_poll）。mode 只差在要不要回 started。"""
        started = time.monotonic()
        not_found_last = _unknown(NOT_FOUND_REASON)
        last = _unknown()
        while True:
            if conn.peer_gone() or self._stopping:
                return None
            if "shutdown" in self.faults:
                return {"status": "error", "error": "server_shutting_down"}
            if "connect_failed" in self.faults or "heartbeat" in self.faults:
                return self._kvm_lost()
            now = time.monotonic()
            elapsed = now - started
            if "not_found" in self.faults:
                last = not_found_last
                if timeout == 0:
                    return {"status": "ok", "data": last}
                if elapsed >= self.not_found_delay and self.not_found_delay <= timeout:
                    return self._fail("log_solution_not_found", "Log Solution Dock icon is not on screen", last)
            else:
                with self._mu:
                    if "timeout" in self.faults:
                        dev.tick()
                        reply, last = None, dev.reading()
                    else:
                        reply, last = self._step(dev, now, mode)
                if reply is not None:
                    return reply
                if timeout == 0:
                    return {"status": "ok", "data": last}
            if elapsed >= timeout:
                return {"status": "timeout", "error": "timeout",
                        "message": f"no reliable result within {timeout:g} s", "data": last}
            limit = timeout
            if "not_found" in self.faults and self.not_found_delay <= timeout:
                limit = self.not_found_delay
            time.sleep(min(self.poll, max(0.01, limit - elapsed)))


def _station_arg(value: str) -> str:
    station = value.strip().upper()
    if station not in SUPPORTED_STATIONS:
        raise argparse.ArgumentTypeError(f"未知站別 '{value}'；支援的站別：{'、'.join(SUPPORTED_STATIONS)}")
    return station


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="模擬 Core（fake core）：讓 LabVIEW 沒有 KVM 也能測 Core Client")
    p.add_argument("--host", default="0.0.0.0", help="監聽位址（預設 0.0.0.0，區網 LabVIEW 電腦可連）")
    p.add_argument("--port", type=int, default=5000, help="TCP port（預設 5000；0 = 系統指派）")
    p.add_argument("--device", default="BT-01", help="設備 ID（預設 BT-01）")
    p.add_argument("--station", default="BT", type=_station_arg,
                   help="站別 BT／DFU／FCT（預設 BT）；FCT 的 monitor_start 成功即視為治具已推入")
    p.add_argument("--channels", type=int, default=4, help="通道數（預設 4）")
    p.add_argument("--finish-delay", type=float, default=5, help="start_test 後幾秒出結果畫面（預設 5）")
    p.add_argument("--monitor-delay", type=float, default=0.5, help="monitor_start 耗時秒數（預設 0.5）")
    p.add_argument("--result", default="PASS", help="PASS / FAIL / TIMEOUT，或逐通道清單 PASS,FAIL,...（預設 PASS）")
    p.add_argument("--not-found-delay", type=float, default=30,
                   help="log_solution_not_found 故障下 check_finish 長輪詢等幾秒才回錯誤（預設 30）")
    p.add_argument("--poll", type=float, default=0.5, help="check_start／check_finish 內部輪詢間隔秒數（預設 0.5）")
    p.add_argument("--scenario", default=None, choices=sorted(set(SCENARIOS) | set(SCENARIO_ALIASES)),
                   help="情境（預設無 = 正常）：" + "、".join(f"{n}({a})" for n, a in SCENARIOS.items()))
    return p


BANNER = """\
模擬 Core 已啟動。在這個視窗輸入指令可手動觸發故障：
{help}
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    try:
        core = FakeCore(args.host, args.port, args.device, args.station, args.channels, args.finish_delay,
                        args.monitor_delay, args.result, args.not_found_delay, args.poll, scenario=args.scenario)
    except ValueError as exc:
        parser.error(str(exc))
    core.start()
    print(f"listening on {core.host}:{core.port}", flush=True)
    if core.host in ("0.0.0.0", ""):
        try:
            print(f"區網連線位址：{socket.gethostbyname(socket.gethostname())}:{core.port}", flush=True)
        except OSError:
            pass
    print(BANNER.format(help=core.HELP), flush=True)
    try:
        # stdin EOF（沒有終端機）不結束伺服器，直到 Ctrl-C 或 quit。
        try:
            for line in sys.stdin:
                out = core.command(line)
                if out:
                    print(out, flush=True)
                if core.quit_event.is_set():
                    break
        except (OSError, ValueError):
            pass
        while not core.quit_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    core.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
