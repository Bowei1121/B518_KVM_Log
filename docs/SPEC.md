# B518 JetKVM_Log 中繼站重構與強健化 Spec

## Problem Statement
The current B518 JetKVM_Log program serves as a relay between the LabVIEW automation system and the ATE test stations via JetKVM. However, it suffers from several critical bottlenecks:
- The single WebRTC connection instance disconnects and reconnects on every device switch, causing massive delays and race conditions.
- A global TCP command lock blocks all incoming LabVIEW requests while any single device is busy, preventing parallel testing.
- The comma-separated TCP protocol is fragile, lacks request tracking, and requires polling which wastes bandwidth and CPU.
- The system lacks a freeze detection guard, risking false-positive test results if a station crashes with a "PASS" screen.
- The core networking and vision processing logic are tightly coupled with the Tkinter GUI, reducing headless stability.

## Solution
We will refactor the system into a headless `Core Service` decoupled from the UI. The core will introduce a `KVM Connection Pool` to maintain concurrent JetKVM WebRTC sessions, a `Device Lock` to serialize commands per machine and fail-fast on conflicts, and a `Long Polling` strategy over a new `JSON Protocol` to eliminate polling overhead. Furthermore, we will implement a `Frame Freeze Guard` using JetKVM Presentation Timestamps to ensure robustness against crashed testing stations. LabVIEW will use `TCP Multiplexing`, keeping one dedicated TCP socket per testing station.

## User Stories
1. As a LabVIEW system, I want to establish a dedicated TCP socket connection per ATE station, so that I can send parallel commands without waiting for other stations to finish.
2. As a LabVIEW system, I want to send commands using a JSON protocol with a Request ID, so that I can reliably parse responses and match them to my asynchronous requests.
3. As a LabVIEW system, I want to receive an immediate `Busy` error if I accidentally send a command to a station that is already processing one, so that I can correct my state machine instead of deadlocking.
4. As a LabVIEW system, I want to issue a long-polling request for visual verification, so that I do not have to spam the network with polling commands while waiting for a test to complete.
5. As a LabVIEW system, I want to send a graceful `shutdown` command, so that the Python service cleanly releases WebRTC and socket resources before exiting.
6. As a Core Service, I want to maintain a pool of active JetKVM WebRTC connections, so that I can instantly take screenshots without paying the signaling and ICE handshake penalty every time a station switches.
7. As a Core Service, I want to prune idle JetKVM connections after 10 minutes, so that I don't leak memory or hold JetKVM hardware resources hostage indefinitely.
8. As a Core Service, I want to periodically grab a background frame from active JetKVM connections, so that I can keep the WebRTC channel alive and detect unexpected hardware disconnects.
9. As a Quality Engineer, I want the system to check the JetKVM Presentation Timestamp before confirming a visual match, so that a frozen "PASS" screen on a crashed ATE does not result in a false-positive yield.
10. As a Maintenance Engineer, I want to run the Monitor UI as a lightweight client that connects to the Core Service via TCP, so that I can inspect patterns and video streams without risking a crash in the main TCP server.

## Implementation Decisions
- **Decoupled Architecture**: `ui_app.py` will be split. The TCP server, WebRTC loop, and computer vision tasks will move to a headless Python daemon. The Tkinter UI will become a TCP client for manual observation.
- **KVMConnectionPool Module**: A new module that maps `kvm_ip` to active `JetKVMClient` instances, handling background heartbeat frames and idle eviction.
- **Per-Device Locking**: Replacing `self._cmd_lock` with a dictionary of locks keyed by `Device No`. If a lock cannot be acquired immediately (non-blocking), return a busy error JSON.
- **JSON Protocol**: The TCP stream will consume newline-delimited JSON. Expected shape: `{"req_id": "...", "cmd": "...", "device": "...", "kvm_ip": "..."}`.
- **Frame Freeze Guard**: The `RoundFrameGate` (currently only in DFU) will be extended to all stations (BT, FCT). It will assert that the `timestamp` attribute of the incoming WebRTC frame increments over a time window.
- **Long Polling Loop**: The command dispatcher for `check` will loop asynchronously (using `asyncio.sleep`) checking the frame status up to the requested timeout, holding the TCP socket open until a result is found.

## Testing Decisions
- We will test the external behavior of the system primarily at the **TCP JSON API seam**.
- We will mock the `JetKVMClient` to yield pre-recorded frames and synthetic timestamps.
- Tests will simulate LabVIEW by opening multiple concurrent `socket` connections to the local Core Service port.
- We will verify that concurrent connections do not block each other (multiplexing), that double-commands on the same connection yield a Busy error (fail-fast), and that mocked static timestamps correctly trigger the Frame Freeze Guard.
- Prior art: We will expand upon the patterns in `test_round_frame_consumer.py` to create the mock KVM frame sources.

## Out of Scope
- Modifying the LabVIEW source code itself (this spec covers the Python relay constraints only).
- Creating new OpenCV template matching heuristics (we are optimizing the infrastructure, not the vision accuracy itself, except for the freeze guard).
- Log file parsing and CSV aggregation (this remains in the separate independent project).

## Further Notes
- A batch file should be provided for TE engineers to easily restart or aggressively kill the Core Service if it encounters unhandled OS-level zombie state.
