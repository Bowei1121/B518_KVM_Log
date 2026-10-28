"""KVM pool heartbeat state (spec #154, issue #157): one lost event on the first failure, one recovered event after."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional

try:
    from kvm_timing import REASON_ZH
except ImportError:
    from .kvm_timing import REASON_ZH  # type: ignore[import-not-found,no-redef]

HEARTBEAT_LOST = "heartbeat_lost"
HEARTBEAT_RECOVERED = "heartbeat_recovered"

logger = logging.getLogger("KVMTiming.heartbeat")


@dataclass(frozen=True)
class HeartbeatEvent:
    kind: str  # HEARTBEAT_LOST | HEARTBEAT_RECOVERED
    ip: str
    reason: str = ""  # lost only
    since_ok_sec: Optional[int] = None  # lost only: whole seconds since the later of the last OK heartbeat and pooling
    outage_sec: Optional[int] = None  # recovered only: whole seconds since the first failure


def _whole_seconds(value: float) -> int:
    """Round half up to whole seconds (never negative)."""
    return max(0, int(math.floor(value + 0.5)))


class HeartbeatTracker:
    """Per-IP heartbeat state, in memory only: a new tracker (new pool) starts clean."""

    def __init__(self) -> None:
        self._last_ok: Dict[str, float] = {}
        self._first_fail: Dict[str, float] = {}

    def observe(self, ip: str, ok: bool, reason: str, now: float, pooled_at: float) -> Optional[HeartbeatEvent]:
        """Record one heartbeat result at monotonic ``now``; return the event it triggers, if any.

        ``pooled_at`` is when the connection entered the pool: ``since_ok_sec`` counts from the later of
        the last OK heartbeat and ``pooled_at``, so an OK heartbeat on an earlier connection does not count.
        """
        if ok:
            failed_at = self._first_fail.pop(ip, None)
            self._last_ok[ip] = now
            if failed_at is None:
                return None
            return HeartbeatEvent(HEARTBEAT_RECOVERED, ip, outage_sec=_whole_seconds(now - failed_at))
        if ip in self._first_fail:
            return None
        self._first_fail[ip] = now
        since = now - max(self._last_ok.get(ip, pooled_at), pooled_at)
        return HeartbeatEvent(HEARTBEAT_LOST, ip, reason=reason, since_ok_sec=_whole_seconds(since))


def log_heartbeat_event(event: HeartbeatEvent, device: str, log: logging.Logger = logger) -> None:
    """Write one bilingual line; WARNING for lost, INFO for recovered."""
    if event.kind == HEARTBEAT_LOST:
        reason_zh = REASON_ZH.get(event.reason, event.reason)
        log.warning(
            f"KVM {event.kind} device={device} ip={event.ip} reason={event.reason} since_ok_sec={event.since_ok_sec}",
            extra={"zh": f"KVM 心跳偵測不到 設備={device} IP={event.ip} 原因={reason_zh} 距上次正常={event.since_ok_sec} 秒"})
        return
    log.info(f"KVM {event.kind} device={device} ip={event.ip} outage_sec={event.outage_sec}",
             extra={"zh": f"KVM 心跳恢復 設備={device} IP={event.ip} 已中斷={event.outage_sec} 秒"})
