"""KVM timing records (spec #154): one Core log line per new KVM connection attempt or KVM heartbeat check."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

BACKGROUND_RECONNECT = "background_reconnect"
COMMAND_CONNECT = "command_connect"

_PATH_ZH = {BACKGROUND_RECONNECT: "背景重連", COMMAND_CONNECT: "指令連線"}
_REASON_ZH = {
    "timeout": "逾時", "connection_refused": "連線被拒", "unreachable": "無法連線", "login_failed": "登入失敗",
    "rpc_not_open": "裝置未回應", "no_signal": "收不到畫面", "cancelled": "已取消", "error": "其他錯誤",
}

logger = logging.getLogger("KVMTiming")


@dataclass(frozen=True)
class TimingRecord:
    ts: float  # epoch seconds at the end of the event
    device: str
    ip: str
    path: str
    elapsed_ms: int
    ok: bool
    reason: str = ""
    command: str = ""
    error_type: str = ""


def classify_connect_error(exc: BaseException) -> str:
    """Plain reason code for a failed connect."""
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    chain = []
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    for e in chain:
        reason = getattr(type(e), "reason", None)
        if isinstance(reason, str) and reason in _REASON_ZH:
            return reason
    for e in chain:
        if isinstance(e, TimeoutError) or any("Timeout" in c.__name__ for c in type(e).__mro__):
            return "timeout"
    for e in chain:
        if isinstance(e, ConnectionRefusedError):
            return "connection_refused"
    if isinstance(exc, OSError):
        return "unreachable"
    return "error"


def log_timing(record: TimingRecord, log: logging.Logger = logger) -> None:
    """Write one bilingual line; INFO when ok, WARNING when failed."""
    path_zh = _PATH_ZH.get(record.path, record.path)
    en = f"KVM timing device={record.device} ip={record.ip} path={record.path}"
    zh = f"KVM 計時 設備={record.device} IP={record.ip} 路徑={path_zh}"
    if record.ok:
        en += f" result=ok elapsed_ms={record.elapsed_ms}"
        zh += f" 結果=成功 耗時={record.elapsed_ms} ms"
        log.info(en, extra={"zh": zh})
        return
    en += f" result=fail elapsed_ms={record.elapsed_ms} reason={record.reason}"
    zh += f" 結果=失敗 耗時={record.elapsed_ms} ms 原因={_REASON_ZH.get(record.reason, record.reason)}"
    if record.error_type:
        en += f" error={record.error_type}"
        zh += f" 例外={record.error_type}"
    log.warning(en, extra={"zh": zh})


# Heartbeat path (issue #157); its lost/recovered events live in kvm_heartbeat.
HEARTBEAT = "heartbeat"
_PATH_ZH[HEARTBEAT] = "心跳"
_REASON_ZH.update({
    "track_ended": "影像中斷", "peer_failed": "連線中斷（失敗）", "peer_closed": "連線中斷（已關閉）",
    "not_connected": "未連線",
})
REASON_ZH = _REASON_ZH  # public read-only view: reason code -> Chinese, for other timing modules
