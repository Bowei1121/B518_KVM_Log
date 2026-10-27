"""Connection failure tracker (issue #134): N consecutive connect failures within a time window.

Pure logic, no Tk and no asyncio. Keyed by canonical KVM IP. The clock is injectable so tests never wait.
The KVM Connection Pool records every failed attempt to establish a new connection and every success here.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional

logger = logging.getLogger("ConnectFailures")

CONNECT_FAILED_ERROR = "kvm_connect_failed"


@dataclass(frozen=True)
class ConnectFailureState:
    """Read-only view of one KVM's consecutive connect failures."""

    fail_count: int = 0
    connect_failed: bool = False
    first_fail_at: Optional[float] = None
    last_error: Optional[str] = None
    next_retry_at: Optional[float] = None
    retrying: bool = False

    def retry_in_sec(self, now: float) -> Optional[int]:
        """Whole seconds (rounded up, never negative) until the next background retry; None when none is pending."""
        if self.next_retry_at is None or self.retrying:
            return None
        return max(0, math.ceil(self.next_retry_at - now))


class _Record:
    def __init__(self, threshold: int) -> None:
        self.fail_count = 0
        self.connect_failed = False
        self.first_fail_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.next_retry_at: Optional[float] = None
        self.retrying = False
        self.recent: Deque[float] = deque(maxlen=threshold)


class ConnectFailures:
    """Per-IP consecutive connect failure counting, alarm decision and retry schedule (memory only)."""

    def __init__(
        self,
        threshold: int = 3,
        window_sec: float = 60.0,
        retry_interval_sec: float = 10.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.clock: Callable[[], float] = clock if clock is not None else time.time
        self._records: Dict[str, _Record] = {}
        self.threshold = int(threshold)
        self.window_sec = float(window_sec)
        self.retry_interval_sec = float(retry_interval_sec)

    def configure(self, threshold: int, window_sec: float, retry_interval_sec: float) -> None:
        """Change the parameters. Counting state and the clock are kept."""
        self.threshold = int(threshold)
        self.window_sec = float(window_sec)
        self.retry_interval_sec = float(retry_interval_sec)
        for record in self._records.values():
            record.recent = deque(record.recent, maxlen=self.threshold)

    def record_failure(self, ip: str, error: str = CONNECT_FAILED_ERROR) -> ConnectFailureState:
        """Count one failed attempt to establish a connection and schedule the next background retry."""
        now = self.clock()
        record = self._records.get(ip)
        if record is None:
            record = self._records[ip] = _Record(self.threshold)
        record.fail_count += 1
        if record.first_fail_at is None:
            record.first_fail_at = now
        record.last_error = error
        record.recent.append(now)
        record.retrying = False
        record.next_retry_at = now + self.retry_interval_sec
        if (
            not record.connect_failed
            and len(record.recent) >= self.threshold
            and record.recent[-1] - record.recent[0] <= self.window_sec
        ):
            record.connect_failed = True
            logger.error(
                "KVM '%s' connection failure: %d consecutive connect failures, last %d within %.1fs",
                ip, record.fail_count, self.threshold, record.recent[-1] - record.recent[0],
                extra={"zh": "KVM '%s' 連線失敗異常：連續 %d 次建立連線失敗，最近 %d 次相隔 %.1f 秒"},
            )
        return self.state(ip)

    def record_success(self, ip: str) -> None:
        """A connection was established: forget everything about this IP and stop its retries."""
        record = self._records.pop(ip, None)
        if record is not None and record.connect_failed:
            logger.info("KVM '%s' connection failure cleared after a successful connection", ip,
                        extra={"zh": "KVM '%s' 連線成功，連線失敗異常已解除"})

    def set_retrying(self, ip: str, retrying: bool) -> None:
        """Mark a background attempt as running (or finished). Ignored for an IP without failures."""
        record = self._records.get(ip)
        if record is not None:
            record.retrying = bool(retrying)

    def due(self, now: Optional[float] = None) -> List[str]:
        """IPs (sorted) whose next retry time has come and that are not being retried right now."""
        if now is None:
            now = self.clock()
        return sorted(
            ip for ip, r in self._records.items()
            if r.next_retry_at is not None and not r.retrying and r.next_retry_at <= now
        )

    def state(self, ip: str) -> ConnectFailureState:
        record = self._records.get(ip)
        if record is None:
            return ConnectFailureState()
        return ConnectFailureState(
            fail_count=record.fail_count,
            connect_failed=record.connect_failed,
            first_fail_at=record.first_fail_at,
            last_error=record.last_error,
            next_retry_at=record.next_retry_at,
            retrying=record.retrying,
        )

    def retry_in_sec(self, ip: str) -> Optional[int]:
        return self.state(ip).retry_in_sec(self.clock())
