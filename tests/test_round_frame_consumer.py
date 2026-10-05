import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from round_frame_consumer import (
    MarkerState, RoundFrameGate, inspect_app_frame, observe_latest_round_frame,
    tcp_round_reply,
)


COLORS = {
    "PASS": (0, 239, 0), "FAIL": (0, 0, 255), "TESTING": (0, 255, 255),
    "NOTEST": (240, 75, 240), "WAITING": (217, 217, 217),
    "COMPLETING": (255, 199, 130), "STOPPED": (191, 191, 191),
    "TIMEOUT": (0, 153, 255),
}
PATTERNS = {
    MarkerState.STANDBY: (1, 0, 0, 1),
    MarkerState.MONITORING: (1, 1, 0, 0),
    MarkerState.REVIEW: (1, 0, 1, 0),
    MarkerState.COMPLETE: (0, 1, 1, 0),
}


def render_frame(state, statuses, scale=2, origin=(23, 31), background=(245, 246, 247)):
    """Render contract 1.0 geometry into a larger, non-zero-origin KVM frame."""
    width, height = round(376 * scale), round(110 * scale)
    ox, oy = origin
    frame = np.full((height + oy, width + ox, 3), background, dtype=np.uint8)

    def rect(x, y, w, h, color):
        x0, y0 = round(ox + x * scale), round(oy + y * scale)
        x1, y1 = round(ox + (x + w) * scale), round(oy + (y + h) * scale)
        frame[y0:y1, x0:x1] = color

    # The asymmetric locator geometry is identical to App contract 1.0.
    for x, inset in ((82, 3), (320, 11)):
        rect(x, 2, 22, 22, (0, 0, 0))
        rect(x + inset, 2 + inset, 8, 8, (255, 255, 255))
    for index, black in enumerate(PATTERNS[state]):
        row, col = divmod(index, 2)
        rect(278 + 2 + col * 12, 2 + row * 12, 10, 10,
             (0, 0, 0) if black else (255, 255, 255))
    for slot in range(1, 21):
        row, col = divmod(slot - 1, 10)
        status = statuses[slot - 1] if slot <= len(statuses) else "OUTSIDE"
        color = (0, 0, 0) if status == "OUTSIDE" else COLORS[status]
        rect(col * 34, 34 + row * 27, 34, 26, color)
    return frame


class RoundFrameConsumerTests(unittest.TestCase):
    def test_frame_recognizes_two_row_capacity_and_each_terminal_result(self):
        statuses = ["PASS", "FAIL", "NOTEST", "TIMEOUT", "PASS", "FAIL",
                    "NOTEST", "PASS", "FAIL", "NOTEST", "PASS", "FAIL"]
        observation = inspect_app_frame(render_frame(MarkerState.COMPLETE, statuses),
                                        received_at=10, now=10)
        self.assertEqual(observation.state, MarkerState.COMPLETE)
        self.assertEqual(observation.capacity, 12)
        self.assertEqual(observation.statuses, tuple(statuses))

    def test_supported_capacities_and_capture_scales(self):
        for capacity in (1, 4, 6, 10, 11, 12, 20):
            for scale in (1.0, 1.5, 2.0, 2.25):
                statuses = (["PASS", "FAIL", "NOTEST", "TIMEOUT"] * 5)[:capacity]
                observation = inspect_app_frame(
                    render_frame(MarkerState.COMPLETE, statuses, scale=scale),
                    received_at=5, now=5,
                )
                with self.subTest(capacity=capacity, scale=scale):
                    self.assertTrue(observation.reliable, observation.reason)
                    self.assertEqual(observation.capacity, capacity)
                    self.assertEqual(observation.statuses, tuple(statuses))

    def test_capacity_external_black_is_not_a_terminal_result_and_capacity_is_contiguous(self):
        self.assertEqual(inspect_app_frame(render_frame(MarkerState.COMPLETE, ["PASS"]),
                                           10, 10).capacity, 1)
        non_contiguous = render_frame(MarkerState.COMPLETE, ["PASS", "PASS", "PASS"])
        x0, y0 = 23, 31
        non_contiguous[y0 + 34 * 2:y0 + 60 * 2, x0 + 34 * 2:x0 + 68 * 2] = (0, 0, 0)
        self.assertEqual(inspect_app_frame(non_contiguous, 10, 10).reason,
                         "non_contiguous_capacity")

    def test_unknown_orientation_occlusion_and_nonterminal_cell_fail_closed(self):
        frame = render_frame(MarkerState.COMPLETE, ["PASS"] * 4)
        rotated = cv2.rotate(frame, cv2.ROTATE_180)
        self.assertFalse(inspect_app_frame(rotated, 10, 10).reliable)
        covered = frame.copy()
        covered[31:90, 170:230] = (255, 255, 255)
        self.assertFalse(inspect_app_frame(covered, 10, 10).reliable)
        waiting = inspect_app_frame(render_frame(MarkerState.COMPLETE,
                                  ["PASS", "WAITING"]), 10, 10)
        self.assertFalse(waiting.results_complete)

    def test_stale_frame_is_rejected(self):
        observation = inspect_app_frame(render_frame(MarkerState.COMPLETE, ["PASS"]),
                                        received_at=2, now=4, max_age=1)
        self.assertFalse(observation.reliable)
        self.assertEqual(observation.reason, "stale_frame")

    def test_blurred_or_corrupted_complete_frame_does_not_become_a_result(self):
        frame = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
        blurred = cv2.GaussianBlur(frame, (11, 11), 4)
        self.assertFalse(inspect_app_frame(blurred, 10, 10).results_complete)
        corrupted = frame.copy()
        corrupted[31 + 4:31 + 56, 23 + 278 * 2:23 + 304 * 2] = (128, 128, 128)
        self.assertFalse(inspect_app_frame(corrupted, 10, 10).results_complete)

    def test_monitoring_arms_gate_review_pauses_and_complete_is_taken_once(self):
        gate = RoundFrameGate("device-1", stable_complete_frames=2, clock=lambda: 10.0)
        monitoring = render_frame(MarkerState.MONITORING, ["WAITING"] * 4)
        review = render_frame(MarkerState.REVIEW, ["PASS", "FAIL", "WAITING", "WAITING"])
        complete = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL", "NOTEST", "TIMEOUT"])
        self.assertEqual(gate.observe(monitoring, 1, 10, now=10).kind, "waiting")
        self.assertEqual(gate.observe(review, 2, 10.1, now=10.1).kind, "paused")
        self.assertEqual(gate.observe(complete, 3, 10.2, now=10.2).kind, "waiting")
        decision = gate.observe(complete, 4, 10.3, now=10.3)
        self.assertEqual(decision.kind, "taken")
        self.assertEqual(decision.results, ((1, "PASS"), (2, "FAIL"),
                                             (3, "NOTEST"), (4, "TIMEOUT")))
        self.assertEqual(gate.observe(complete, 5, 10.4, now=10.4).kind, "already_taken")

    def test_startup_residual_complete_never_takes_without_monitoring(self):
        gate = RoundFrameGate("device-1", stable_complete_frames=2, clock=lambda: 10.0)
        complete = render_frame(MarkerState.COMPLETE, ["PASS"] * 4)
        self.assertEqual(gate.observe(complete, 1, 10, now=10).kind, "waiting")
        self.assertEqual(gate.observe(complete, 2, 10.1, now=10.1).kind, "waiting")
        self.assertFalse(gate.taken)

    def test_new_round_monitoring_rearms_and_duplicate_or_old_sequence_cannot_vote(self):
        gate = RoundFrameGate("device-1", stable_complete_frames=1, clock=lambda: 5.0)
        monitoring = render_frame(MarkerState.MONITORING, ["WAITING"] * 4)
        complete = render_frame(MarkerState.COMPLETE, ["PASS"] * 4)
        self.assertEqual(gate.observe(monitoring, 1, 1, now=1).kind, "waiting")
        self.assertEqual(gate.observe(complete, 2, 2, now=2).kind, "taken")
        self.assertEqual(gate.observe(monitoring, 3, 3, now=3).kind, "waiting")
        self.assertEqual(gate.observe(complete, 3, 4, now=4).kind, "waiting")
        self.assertEqual(gate.observe(complete, 4, 5, now=5).kind, "taken")

    def test_device_gates_do_not_share_taken_state(self):
        first = RoundFrameGate("one", stable_complete_frames=1)
        second = RoundFrameGate("two", stable_complete_frames=1)
        monitoring = render_frame(MarkerState.MONITORING, ["WAITING"] * 2)
        complete = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL"])
        self.assertEqual(first.observe(monitoring, 1, 1, now=1).kind, "waiting")
        self.assertEqual(first.observe(complete, 2, 2, now=2).kind, "taken")
        self.assertEqual(second.observe(complete, 1, 2, now=2).kind, "waiting")
        self.assertFalse(second.taken)

    def test_reconnect_requires_monitoring_again_before_residual_complete_can_be_taken(self):
        gate = RoundFrameGate("device-1", stable_complete_frames=1, clock=lambda: 4.0)
        monitoring = render_frame(MarkerState.MONITORING, ["WAITING"] * 2)
        complete = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL"])
        self.assertEqual(gate.observe(monitoring, 1, 1, now=1, stream_id="stream-a").kind,
                         "waiting")
        self.assertEqual(gate.observe(complete, 2, 2, now=2, stream_id="stream-a").kind,
                         "taken")
        self.assertEqual(gate.observe(complete, 1, 3, now=3, stream_id="stream-b").kind,
                         "waiting")
        self.assertFalse(gate.taken)

    def test_complete_frame_spacing_and_same_image_status_changes_are_checked(self):
        gate = RoundFrameGate("device-1", stable_complete_frames=2,
                              max_frame_gap=0.5, clock=lambda: 4.0)
        monitoring = render_frame(MarkerState.MONITORING, ["WAITING"] * 2)
        complete = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL"])
        changed = render_frame(MarkerState.COMPLETE, ["PASS", "PASS"])
        gate.observe(monitoring, 1, 1, now=1)
        self.assertEqual(gate.observe(complete, 2, 2, now=2).kind, "waiting")
        self.assertEqual(gate.observe(complete, 3, 3, now=3).kind, "waiting")
        self.assertEqual(gate.observe(changed, 4, 3.1, now=3.1).kind, "waiting")
        self.assertEqual(gate.observe(changed, 5, 3.2, now=3.2).kind, "taken")

    def test_public_latest_frame_interface_and_tcp_reply_are_one_shot(self):
        class FakeKvm:
            def __init__(self):
                self.frame = render_frame(MarkerState.MONITORING, ["WAITING"] * 2)
                self.frame_sequence = 1
                self.frame_received_monotonic = 10
                self.stream_id = "controlled"

            def latest_frame(self):
                return self.frame.copy(), self.frame_sequence, self.frame_received_monotonic, self.stream_id

        kvm = FakeKvm()
        gate = RoundFrameGate("device-1", stable_complete_frames=1, clock=lambda: 12)
        decision = observe_latest_round_frame(kvm, gate, now=10)
        self.assertEqual(decision.kind, "waiting")
        self.assertEqual(tcp_round_reply(decision), "action_waiting,monitoring\r\n")
        kvm.frame = render_frame(MarkerState.REVIEW, ["PASS", "FAIL"])
        kvm.frame_sequence += 1
        kvm.frame_received_monotonic += 0.1
        decision = observe_latest_round_frame(kvm, gate, now=10.1)
        self.assertEqual(decision.kind, "paused")
        self.assertEqual(tcp_round_reply(decision), "action_paused,review\r\n")
        kvm.frame = render_frame(MarkerState.COMPLETE, ["PASS", "FAIL"])
        kvm.frame_sequence += 1
        kvm.frame_received_monotonic += 0.1
        decision = observe_latest_round_frame(kvm, gate, now=10.2)
        self.assertEqual(decision.kind, "taken")
        self.assertEqual(tcp_round_reply(decision), "action_done,1::PASS,2::FAIL\r\n")
        kvm.frame_sequence += 1
        kvm.frame_received_monotonic += 0.1
        decision = observe_latest_round_frame(kvm, gate, now=10.3)
        self.assertEqual(decision.kind, "already_taken")
        self.assertNotEqual(tcp_round_reply(decision), "action_done,1::PASS,2::FAIL\r\n")


if __name__ == "__main__":
    unittest.main()
