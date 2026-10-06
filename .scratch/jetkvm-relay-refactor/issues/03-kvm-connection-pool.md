# 03 — KVM Connection Pool with Heartbeat & Idle Eviction

**What to build:** A persistent KVM connection pool that maintains active WebRTC connections keyed by KVM IP address. Sequential commands targeting the same KVM reuse the established session, avoiding redundant WebRTC signaling and ICE handshakes. Active connections periodically capture background frames (heartbeat) to keep NAT/WebRTC channels alive. Connections that remain unused for longer than 10 minutes are automatically pruned and closed.

**Blocked by:** 01 — Headless TCP JSON Protocol & Multiplexing Server

**Status:** completed

- [x] The core service reuses active WebRTC connections when consecutive requests target the same KVM IP.
- [x] A background worker periodically pulls heartbeat frames from open connections to prevent channel timeout.
- [x] Any connection idle for more than 10 minutes is cleanly evicted and disconnected.
- [x] A subsequent command to an evicted KVM transparently re-establishes a fresh WebRTC connection.
- [x] Comprehensive tests with a mock KVM client verify connection reuse, heartbeat triggers, and idle eviction timing.
