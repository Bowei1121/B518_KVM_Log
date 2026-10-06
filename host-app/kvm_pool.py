"""KVM Connection Pool with Heartbeat & Idle Eviction.

Maintains persistent WebRTC connections keyed by KVM IP/host.
Reuses established sessions across consecutive commands, periodically
captures background frames (heartbeat) to keep NAT/WebRTC channels alive and detect
disconnects, and automatically evicts connections that remain idle for >10 minutes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("KVMConnectionPool")


def normalize_kvm_ip(ip: Any) -> str:
    """Normalize a KVM IP/host identifier.
    
    Strips protocol schemes (http://, https://, ws://, wss://), surrounding whitespace,
    and trailing slashes.
    """
    if ip is None:
        raise ValueError("KVM IP cannot be None")
    s = str(ip).strip()
    if not s:
        raise ValueError("KVM IP cannot be empty")

    for scheme in ("http://", "https://", "ws://", "wss://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break

    s = s.rstrip("/")
    if not s:
        raise ValueError("KVM IP cannot be empty after normalization")
    return s


def _default_client_factory(host: str, password: str = "") -> Any:
    """Default factory creating real JetKVMClient instances."""
    try:
        from jetkvm_core import JetKVMClient
    except ImportError:
        try:
            from .jetkvm_core import JetKVMClient  # type: ignore[import-not-found,no-redef]
        except ImportError as exc:
            raise RuntimeError(f"JetKVMClient could not be imported: {exc}") from exc
    return JetKVMClient(host, password=password)


class PooledKVMConnection:
    """Represents a managed KVM WebRTC connection and its activity metadata."""

    def __init__(self, ip: str, client: Any) -> None:
        self.ip = ip
        self.client = client
        now = time.time()
        self.created_at = now
        self.last_used_at = now
        self.last_heartbeat_at = 0.0
        self.heartbeat_count = 0
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock


class KVMConnectionPool:
    """Thread-safe and async-native connection pool for KVM WebRTC sessions."""

    def __init__(
        self,
        idle_timeout: float = 600.0,
        heartbeat_interval: float = 5.0,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.idle_timeout = float(idle_timeout)
        self.heartbeat_interval = float(heartbeat_interval)
        self._client_factory = client_factory if client_factory is not None else _default_client_factory

        self._pool: Dict[str, PooledKVMConnection] = {}
        self._ip_locks: Dict[str, asyncio.Lock] = {}
        self._pool_lock: Optional[asyncio.Lock] = None

        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        """Return True if the background maintenance worker is running."""
        return self._is_running

    def _get_pool_lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    def _get_ip_lock(self, ip: str) -> asyncio.Lock:
        if ip not in self._ip_locks:
            self._ip_locks[ip] = asyncio.Lock()
        return self._ip_locks[ip]

    def _instantiate_client(self, host: str, password: str = "") -> Any:
        try:
            return self._client_factory(host, password=password)
        except TypeError:
            return self._client_factory(host)

    async def _safe_call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        res = fn(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res

    async def get_connection(self, ip: str, password: str = "") -> Any:
        """Get an active WebRTC connection for the given KVM IP.
        
        Reuses an existing healthy connection if available, or transparently
        connects a fresh instance.
        """
        canonical_ip = normalize_kvm_ip(ip)
        ip_lock = self._get_ip_lock(canonical_ip)

        async with ip_lock:
            now = time.time()
            entry = self._pool.get(canonical_ip)

            if entry is not None:
                client = entry.client
                if getattr(client, "connected", True):
                    entry.last_used_at = now
                    logger.debug("Reusing existing KVM connection for '%s'", canonical_ip)
                    return client
                else:
                    logger.warning("Existing KVM connection for '%s' is closed; discarding", canonical_ip)
                    await self._evict_entry(canonical_ip)

            logger.info("Connecting to KVM '%s'...", canonical_ip)
            client = self._instantiate_client(canonical_ip, password=password)

            if hasattr(client, "connect"):
                await self._safe_call(getattr(client, "connect"))

            entry = PooledKVMConnection(canonical_ip, client)
            self._pool[canonical_ip] = entry
            logger.info("KVM connection established and pooled for '%s'", canonical_ip)
            return client

    async def perform_heartbeat(self) -> int:
        """Pull heartbeat frames from all active connections in the pool.
        
        Returns the number of successful heartbeats.
        """
        entries = list(self._pool.values())
        if not entries:
            return 0

        success_count = 0
        now = time.time()

        for entry in entries:
            client = entry.client
            ip = entry.ip

            if not getattr(client, "connected", True):
                logger.warning("KVM '%s' disconnected during heartbeat; evicting", ip)
                await self._evict_entry(ip)
                continue

            try:
                if hasattr(client, "latest_frame"):
                    await self._safe_call(getattr(client, "latest_frame"))
                elif hasattr(client, "grab_frame"):
                    await self._safe_call(getattr(client, "grab_frame"))
                elif hasattr(client, "heartbeat"):
                    await self._safe_call(getattr(client, "heartbeat"))

                entry.last_heartbeat_at = now
                entry.heartbeat_count += 1
                success_count += 1
                logger.debug("Heartbeat pulled for KVM '%s' (total=%d)", ip, entry.heartbeat_count)
            except Exception as exc:
                logger.warning("Heartbeat failed for KVM '%s': %s; evicting connection", ip, exc)
                await self._evict_entry(ip)

        return success_count

    async def prune_idle_connections(self, max_idle: Optional[float] = None) -> int:
        """Evict connections that have remained idle longer than the timeout.
        
        Returns the number of evicted connections.
        """
        timeout = max_idle if max_idle is not None else self.idle_timeout
        now = time.time()
        to_evict: List[str] = []

        for ip, entry in list(self._pool.items()):
            idle_time = now - entry.last_used_at
            if idle_time > timeout:
                to_evict.append(ip)

        for ip in to_evict:
            target_entry = self._pool.get(ip)
            idle_duration = (now - target_entry.last_used_at) if target_entry is not None else timeout
            logger.info("Evicting idle KVM connection '%s' (idle %.1fs > %.1fs)", ip, idle_duration, timeout)
            await self._evict_entry(ip)

        return len(to_evict)

    async def _close_entry(self, entry: PooledKVMConnection) -> None:
        client = entry.client
        try:
            if hasattr(client, "close"):
                await self._safe_call(getattr(client, "close"))
        except Exception as exc:
            logger.warning("Error closing KVM client '%s': %s", entry.ip, exc)

    async def _evict_entry(self, ip: str) -> None:
        entry = self._pool.pop(ip, None)
        if entry is not None:
            await self._close_entry(entry)

    async def close_connection(self, ip: str) -> None:
        """Explicitly evict and close a connection by IP."""
        try:
            canonical_ip = normalize_kvm_ip(ip)
        except ValueError:
            return
        await self._evict_entry(canonical_ip)

    async def _maintenance_worker(self) -> None:
        """Background worker that periodically triggers heartbeats and idle pruning."""
        logger.info("KVM pool background worker started.")
        try:
            while self._is_running:
                await asyncio.sleep(self.heartbeat_interval)
                if not self._is_running:
                    break
                await self.perform_heartbeat()
                await self.prune_idle_connections()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected error in KVM pool maintenance worker")
        finally:
            logger.info("KVM pool background worker stopped.")

    async def start(self) -> None:
        """Start the background maintenance worker."""
        if self._is_running:
            return
        self._is_running = True
        loop = asyncio.get_running_loop()
        self._worker_task = loop.create_task(self._maintenance_worker())

    async def stop(self) -> None:
        """Stop the background maintenance worker."""
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def close_all(self) -> None:
        """Stop worker and close all active connections cleanly."""
        await self.stop()
        for ip in list(self._pool.keys()):
            await self._evict_entry(ip)
        self._pool.clear()

    def active_count(self) -> int:
        """Return the number of active connections in the pool."""
        return len(self._pool)

    def get_active_ips(self) -> List[str]:
        """Return sorted list of active KVM IPs currently in the pool."""
        return sorted(self._pool.keys())

    def is_connected(self, ip: str) -> bool:
        """Check if an active, connected session exists for the given IP."""
        try:
            canonical_ip = normalize_kvm_ip(ip)
        except ValueError:
            return False
        entry = self._pool.get(canonical_ip)
        if entry is None:
            return False
        return bool(getattr(entry.client, "connected", True))

    def get_connection_info(self, ip: str) -> Optional[Dict[str, Any]]:
        """Return activity metadata for a specific pooled connection."""
        try:
            canonical_ip = normalize_kvm_ip(ip)
        except ValueError:
            return None
        entry = self._pool.get(canonical_ip)
        if entry is None:
            return None
        now = time.time()
        return {
            "ip": entry.ip,
            "connected": bool(getattr(entry.client, "connected", True)),
            "created_at": entry.created_at,
            "last_used_at": entry.last_used_at,
            "idle_seconds": round(now - entry.last_used_at, 2),
            "last_heartbeat_at": entry.last_heartbeat_at,
            "heartbeat_count": entry.heartbeat_count,
        }

    def get_pool_status(self) -> Dict[str, Any]:
        """Return high-level status of the connection pool."""
        return {
            "active_connections": self.active_count(),
            "active_ips": self.get_active_ips(),
            "idle_timeout": self.idle_timeout,
            "heartbeat_interval": self.heartbeat_interval,
            "is_running": self._is_running,
        }
