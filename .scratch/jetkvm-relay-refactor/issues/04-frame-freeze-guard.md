# 04 — Frame Freeze Guard Across All Stations

**What to build:** A Presentation Timestamp (PTS) freeze guard applied to visual inspection across all ATE test stations (BT, FCT, DFU). Before confirming a visual template match or log reading, the system verifies that the WebRTC frame's PTS has advanced over the inspection window. If the video stream is frozen (timestamps remain static), the check fails fast with a frozen frame error, preventing crashed ATE machines displaying static PASS screens from generating false-positive test results.

**Blocked by:** 03 — KVM Connection Pool with Heartbeat & Idle Eviction

**Status:** ready-for-agent

- [ ] All station vision checks verify that the incoming frame's Presentation Timestamp is incrementing.
- [ ] If the timestamp does not change within the designated threshold window, the system returns `{"status": "error", "error": "frame_frozen"}`.
- [ ] Live frames with normal timestamp progression pass verification normally.
- [ ] Automated tests using mock KVM sources with static and dynamic timestamps confirm freeze detection behavior.
