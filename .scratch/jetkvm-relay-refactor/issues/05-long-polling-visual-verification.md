# 05 — Long-Polling Visual Verification Command

**What to build:** An asynchronous long-polling `check` command over TCP. LabVIEW issues a single request with a timeout (e.g., 30s). The Core Service holds the TCP connection open, periodically evaluating the live KVM frame against target visual patterns until either a match is found or the timeout expires. This eliminates high-frequency polling packets from LabVIEW.

```json
// Request
{"req_id": "req-003", "cmd": "check", "device": "1", "kvm_ip": "192.168.1.10", "timeout_sec": 30}

// Success Response
{"req_id": "req-003", "status": "ok", "result": "PASS", "elapsed_sec": 4.2}

// Timeout Response
{"req_id": "req-003", "status": "timeout", "result": "NONE", "elapsed_sec": 30.0}
```

**Blocked by:** 02 — Per-Device Lock & Fail-Fast Busy Response, 04 — Frame Freeze Guard Across All Stations

**Status:** completed

- [x] Client can send a `check` command with a specified `timeout_sec`.
- [x] TCP connection remains open while the server evaluates frames asynchronously in the background.
- [x] Returns immediately with the matching result once visual conditions are met.
- [x] Returns `timeout` status if no match occurs within `timeout_sec`.
- [x] Integrates device locking and frame freeze guards during the evaluation loop.
- [x] End-to-end integration test verifies long-polling lifecycle without socket starvation.
