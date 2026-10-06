# 01 — Headless TCP JSON Protocol & Multiplexing Server

**What to build:** A standalone, headless TCP server that communicates with LabVIEW and other clients using newline-delimited JSON. It must support multiple concurrent TCP socket connections (one per testing station), assign and echo back `req_id` to correlate requests and responses, handle basic ping/status requests, and support a graceful `shutdown` command that cleanly terminates the server.

```json
// Request
{"req_id": "req-001", "cmd": "ping"}

// Response
{"req_id": "req-001", "status": "ok", "data": "pong"}
```

**Blocked by:** None — can start immediately

**Status:** completed

- [x] A TCP server starts headlessly without requiring any UI or desktop display loop.
- [x] Multiple TCP clients can connect simultaneously without blocking each other.
- [x] Server parses newline-delimited JSON requests and replies with matching `req_id`.
- [x] Malformed JSON or unknown commands return an explicit JSON error without crashing the server.
- [x] Sending `{"cmd": "shutdown"}` gracefully closes all active socket connections and terminates the TCP server loop.
- [x] Automated end-to-end TCP socket tests pass.
