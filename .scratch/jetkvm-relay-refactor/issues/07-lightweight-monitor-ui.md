# 07 — Lightweight Monitor UI Client

**What to build:** Refactor the existing Tkinter application into a decoupled TCP client. The UI connects to the running Core Service to inspect live KVM streams, observe device statuses, and manually trigger diagnostic checks. The UI is completely isolated: closing or crashing the GUI process does not disrupt the Core Service or active LabVIEW test runs.

**Blocked by:** 05 — Long-Polling Visual Verification Command, 06 — Core Daemon Script & TE Process Management Utilities

**Status:** complete

- [x] Tkinter UI runs as a separate process and connects to Core Service via TCP.
- [x] UI visualizes real-time status of all configured test stations.
- [x] UI can request and display snapshots/streams without interfering with LabVIEW automated testing.
- [x] Terminating or restarting the UI does not affect ongoing Core Service operations or active LabVIEW TCP sockets.
