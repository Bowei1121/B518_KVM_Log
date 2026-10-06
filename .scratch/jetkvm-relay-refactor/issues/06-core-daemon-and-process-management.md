# 06 — Core Daemon Script & TE Process Management Utilities

**What to build:** Headless process packaging and management scripts for test engineering (TE) operators. The Core Service runs independently of any desktop GUI. Scripts are provided for starting, graceful stopping (via TCP `shutdown`), status querying, and aggressive recovery (killing orphan/zombie Python processes if hardware or network hangs).

**Blocked by:** 01 — Headless TCP JSON Protocol & Multiplexing Server

**Status:** ready-for-agent

- [ ] Core service can be launched headlessly from command-line without DISPLAY or Tkinter dependencies.
- [ ] Management script can cleanly trigger graceful shutdown via TCP command and confirm process termination.
- [ ] Force-kill script cleanly cleans up orphaned processes and releases ports.
- [ ] Logs are written to rotating log files with timestamp and log levels.
