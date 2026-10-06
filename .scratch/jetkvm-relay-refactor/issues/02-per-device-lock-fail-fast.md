# 02 — Per-Device Lock & Fail-Fast Busy Response

**What to build:** Per-device non-blocking command serialization. When multiple concurrent TCP connections send commands targeting the same device, the active command proceeds while any overlapping command on that same device immediately fails fast with a `busy` status rather than hanging or blocking other devices. Commands targeting different devices execute independently and in parallel.

```json
// Busy Response
{"req_id": "req-002", "status": "busy", "error": "device_busy", "device": "1"}
```

**Blocked by:** 01 — Headless TCP JSON Protocol & Multiplexing Server

**Status:** ready-for-agent

- [ ] Commands target specific devices via a `device` field.
- [ ] Concurrent requests to different devices execute simultaneously without contention.
- [ ] If a command is actively running on device A, any incoming command targeting device A immediately returns `{"status": "busy", "error": "device_busy"}` without blocking.
- [ ] When the active command on device A completes, subsequent commands targeting device A can be processed normally.
- [ ] Integration tests verify parallel multi-device execution and immediate fail-fast behavior on single-device conflict.
