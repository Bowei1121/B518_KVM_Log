"""Per-device locking and concurrency management.

Provides non-blocking per-device mutual exclusion to support fail-fast
behavior when concurrent requests target the same device.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger("DeviceLockManager")


class DeviceBusyError(Exception):
    """Raised when an operation cannot acquire a device lock."""

    def __init__(self, device: str, req_id: Optional[str] = None) -> None:
        self.device = str(device)
        self.req_id = req_id
        super().__init__(f"Device '{self.device}' is busy (req_id={self.req_id})")


class DeviceLockManager:
    """Manages per-device locks with non-blocking fail-fast semantics."""

    def __init__(self) -> None:
        self._manager_lock = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}
        self._owners: Dict[str, Dict[str, Any]] = {}

    def _normalize_key(self, device: Any) -> str:
        """Normalize a device identifier into a non-empty string."""
        if device is None:
            raise ValueError("Device identifier cannot be None")
        key = str(device).strip()
        if not key:
            raise ValueError("Device identifier cannot be empty")
        return key

    def try_acquire(self, device: Any, req_id: Optional[str] = None) -> bool:
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
            with self._manager_lock:
                self._owners[key] = {
                    "req_id": req_id,
                    "acquired_at": time.time(),
                }
            logger.debug("Acquired lock for device '%s' (req_id=%s)", key, req_id)
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
            self._owners.pop(key, None)

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

    def get_owner(self, device: Any) -> Optional[Dict[str, Any]]:
        """Get ownership metadata for an active device lock, if any."""
        try:
            key = self._normalize_key(device)
        except ValueError:
            return None
        with self._manager_lock:
            return dict(self._owners.get(key, {})) if key in self._owners else None

    def get_active_devices(self) -> List[str]:
        """Return list of device IDs that are currently locked."""
        with self._manager_lock:
            return [k for k, lock in self._locks.items() if lock.locked()]

    @contextmanager
    def lock_device(self, device: Any, req_id: Optional[str] = None):
        """Context manager for non-blocking device locking.

        Raises DeviceBusyError if the device is currently locked.
        """
        key = self._normalize_key(device)
        if not self.try_acquire(key, req_id=req_id):
            raise DeviceBusyError(device=key, req_id=req_id)
        try:
            yield
        finally:
            self.release(key)
