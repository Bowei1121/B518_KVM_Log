"""Per-device locking and concurrency management.

Provides non-blocking per-device mutual exclusion to support fail-fast
behavior when concurrent requests target the same device.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List

logger = logging.getLogger("DeviceLockManager")


class DeviceLockManager:
    """Manages per-device locks with non-blocking fail-fast semantics."""

    def __init__(self) -> None:
        self._manager_lock = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}

    def _normalize_key(self, device: Any) -> str:
        """Normalize a device identifier into a non-empty string."""
        if device is None:
            raise ValueError("Device identifier cannot be None")
        key = str(device).strip()
        if not key:
            raise ValueError("Device identifier cannot be empty")
        return key

    def try_acquire(self, device: Any) -> bool:
        """Attempt to acquire the lock for a specific device without blocking.

        Returns True if acquired, False if already locked (busy).
        """
        key = self._normalize_key(device)
        with self._manager_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            lock = self._locks[key]

        acquired = lock.acquire(blocking=False)
        if acquired:
            logger.debug("Acquired lock for device '%s'", key)
        else:
            logger.debug("Failed to acquire lock for device '%s' (busy)", key)
        return acquired

    def release(self, device: Any) -> None:
        """Release the lock for a specific device."""
        try:
            key = self._normalize_key(device)
        except ValueError:
            return

        with self._manager_lock:
            lock = self._locks.get(key)

        if lock is not None:
            try:
                lock.release()
                logger.debug("Released lock for device '%s'", key)
            except RuntimeError:
                # Lock was not acquired / already unlocked
                pass

    def is_locked(self, device: Any) -> bool:
        """Check if a specific device is currently locked."""
        try:
            key = self._normalize_key(device)
        except ValueError:
            return False
        with self._manager_lock:
            lock = self._locks.get(key)
            return lock.locked() if lock is not None else False

    def get_active_devices(self) -> List[str]:
        """Return list of device IDs that are currently locked."""
        with self._manager_lock:
            return [k for k, lock in self._locks.items() if lock.locked()]
