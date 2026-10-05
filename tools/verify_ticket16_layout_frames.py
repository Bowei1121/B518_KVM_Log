"""Read saved raw App pixels through the upper-computer frame and action seams."""

import argparse
import json
from pathlib import Path
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-app"))
from round_frame_consumer import CONTRACT_VERSION, RoundFrameGate, tcp_round_reply


def verify(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "layouts.json").read_text(encoding="utf-8"))
    if metadata["contract_version"] != CONTRACT_VERSION:
        raise RuntimeError("Paired display contract version mismatch")
    rounds = []
    for expected in metadata["rounds"]:
        gate = RoundFrameGate("controlled-layout", max_frame_gap=2)
        actions, frames = [], []
        for sequence, name in enumerate(("monitoring.png", "complete.png", "complete-scrolled.png"), 1):
            path = directory / "capacity-{}".format(expected["capacity"]) / name
            frame = cv2.imread(str(path))
            decision = gate.observe(frame, sequence, float(sequence), now=float(sequence),
                                    stream_id="offline")
            reply = tcp_round_reply(decision)
            if reply.startswith("action_done,"):
                actions.append(reply.strip())
            frames.append(dict(file=str(path.relative_to(directory)), decision=decision.kind,
                               reason=decision.reason, capacity=decision.capacity,
                               reply=reply.strip()))
        expected_results = tuple(enumerate(expected["statuses"], 1))
        if ([item["decision"] for item in frames] != ["waiting", "waiting", "taken"]
                or decision.results != expected_results or len(actions) != 1):
            raise RuntimeError("Live Tk frame round mismatch: {}".format(frames))
        rounds.append(dict(capacity=expected["capacity"], frames=frames,
                           expected_results=expected_results, fake_action_requests=actions))
    return dict(contract_version=CONTRACT_VERSION, rounds=rounds,
                is_jetkvm_frame=False, timestamp_basis="synthetic ordering of offline captures",
                device_actions_performed=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-evidence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify(args.app_evidence)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PASS: {} real Tk rounds through raw frame and fake action seams".format(len(result["rounds"])))


if __name__ == "__main__":
    main()
