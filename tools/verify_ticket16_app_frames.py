"""Run B518 Tk/Quartz captures through the actual upper-computer frame seam."""

import argparse
import json
from pathlib import Path
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from round_frame_consumer import CONTRACT_VERSION, RoundFrameGate, inspect_app_frame, tcp_round_reply


EXPECTED = {
    "standby": "standby",
    "monitoring": "monitoring",
    "review": "review",
    "complete": "complete",
    "details-scrolled": "complete",
}


def verify(evidence_dir):
    evidence_dir = Path(evidence_dir)
    gate = RoundFrameGate("ticket16-controlled-device", stable_complete_frames=2,
                          max_age=10.0, max_frame_gap=2.0)
    records = []
    fake_action_requests = []
    for sequence, (name, expected) in enumerate(EXPECTED.items(), 1):
        path = evidence_dir / (name + ".png")
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("缺少可讀取的 Tk/Quartz 畫面：{}".format(path))
        received_at = float(sequence)
        observation = inspect_app_frame(frame, received_at, received_at)
        if not observation.reliable or observation.state.value != expected:
            raise RuntimeError("{} 辨識失敗：expected={}, got={}, reason={}".format(
                name, expected, getattr(observation.state, "value", None), observation.reason))
        decision = gate.observe(frame, sequence, received_at, now=received_at,
                                stream_id="quartz-controlled-capture")
        reply = tcp_round_reply(decision)
        if reply.startswith("action_done,"):
            fake_action_requests.append(reply.strip())
        records.append({
            "file": path.name,
            "expected_state": expected,
            "recognized_state": observation.state.value,
            "capacity": observation.capacity,
            "statuses": list(observation.statuses),
            "frame_pixels": [int(frame.shape[1]), int(frame.shape[0])],
            "detected_scale": round(observation.scale, 4),
            "detected_app_origin_pixels": [round(value, 2) for value in observation.app_origin],
            "gate_decision": decision.kind,
            "gate_reason": decision.reason,
            "upper_reply": reply.strip(),
        })
    if [record["gate_decision"] for record in records] != [
            "waiting", "waiting", "paused", "waiting", "taken"]:
        raise RuntimeError("輪次閘門決策順序不符：{}".format(
            [record["gate_decision"] for record in records]))
    taken = records[-1]
    if taken["capacity"] != 20 or len(taken["statuses"]) != 20:
        raise RuntimeError("受控完成畫面未辨識完整二十格結果。")
    if len(fake_action_requests) != 1:
        raise RuntimeError("受控同輪未產生唯一一次假動作出口請求。")
    return {
        "source": "B518 Log Solution real Tk window captured locally through Quartz",
        "is_jetkvm_frame": False,
        "presentation_timestamp_basis": "synthetic ordering for offline screenshots; not JetKVM source PTS",
        "contract_version": CONTRACT_VERSION,
        "sequence": records,
        "taken_results": [[slot, status] for slot, status in enumerate(taken["statuses"], 1)],
        "fake_action_requests": fake_action_requests,
        "assertions": {
            "review_pauses": True,
            "monitoring_arms": True,
            "complete_requires_two_fresh_captures": True,
            "result_taken_once": True,
            "fake_action_outlet_recorded_once": True,
            "capacity_external_black_cells_excluded": True,
            "real_jetkvm_and_device_action_tested": False,
            "jetkvm_source_presentation_timestamp_tested": False,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-evidence", required=True,
                        help="Directory containing Ticket 12/16 Tk/Quartz PNG captures")
    parser.add_argument("--output", required=True, help="Path for the replay JSON report")
    args = parser.parse_args()
    report = verify(args.app_evidence)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS: {} App captures; result release={}, capacity={}".format(
        len(report["sequence"]), report["sequence"][-1]["gate_decision"],
        report["sequence"][-1]["capacity"]))
    print("Report: {}".format(output))


if __name__ == "__main__":
    main()
