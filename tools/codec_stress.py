"""H.264 round-trip stress for the Log Solution display-contract 1.3 reader.

Renders contract-1.3 frames (state marker, 16-state heartbeat, result band) onto a busy
1920x1080 desktop, encodes them with libx264 (zerolatency) at several bitrates and output
sizes, decodes, and reads every frame back through ``log_solution_reader.inspect_app_frame``.

It answers two questions offline, before field verification (docs/FIELD_VERIFICATION.md L1/L2):

* misread: a frame read as reliable but with the wrong state, capacity, slot results or
  heartbeat. Must stay 0 in every case; the exit code is 1 otherwise.
* readable: how many frames are reliable. Low bitrate or a downscaled stream lowers it; an
  unreadable frame is reported as not visible, never as a wrong result.

This is a simulation with libx264, not JetKVM's hardware encoder. Requires PyAV with libx264
(the project venv has it).

Examples::

    python tools/codec_stress.py
    python tools/codec_stress.py --tk-scales 1 1.5 --sizes 1920x1080 1280x720 --bitrates 1500 400
    python tools/codec_stress.py --mac-scaled 2880 3840 --json result.json
"""

import argparse
import io
import json
import random
import sys
from pathlib import Path

import av
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host-app"))
sys.path.insert(0, str(ROOT / "tests"))
from log_solution_reader import CONTRACT_VERSION, inspect_app_frame  # noqa: E402
from protocol_helpers import _LOG_COLORS, heartbeat_bits, render_log_solution  # noqa: E402

STATES = ("standby", "preparing", "monitoring", "review", "complete", "manual_stop_complete")
COLORS = tuple(name for name in _LOG_COLORS if name != "OUTSIDE")
CAPTURE = (1920, 1080)


def busy_desktop(width, height, seed=2):
    """Gradient, random UI blocks and text-like noise, so the encoder spends bits elsewhere too."""
    ramp = np.tile(np.linspace(40, 200, width, dtype=np.uint8), (height, 1))
    image = cv2.merge([ramp, ramp[:, ::-1], np.full((height, width), 120, np.uint8)])
    rng = np.random.default_rng(seed)
    for _ in range(80):
        x, y = int(rng.integers(0, width - 200)), int(rng.integers(0, height - 100))
        cv2.rectangle(image, (x, y), (x + int(rng.integers(40, 300)), y + int(rng.integers(20, 120))),
                      tuple(int(c) for c in rng.integers(0, 255, 3)), -1)
    for _ in range(400):
        cv2.putText(image, "SN%06d" % rng.integers(0, 999999),
                    (int(rng.integers(0, width - 120)), int(rng.integers(10, height))),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (int(rng.integers(0, 255)),) * 3, 1)
    return image


def make_sequence(tk_scale, frames=180, fps=30, seed=1, canvas=CAPTURE):
    """Frames on ``canvas`` with the App drawn at ``tk_scale`` px per Tk unit, and the truth for each."""
    rng = random.Random(seed)
    desktop = busy_desktop(*canvas)
    capacity = rng.choice((4, 6, 20))
    state, statuses = "monitoring", [rng.choice(COLORS) for _ in range(capacity)]
    images, truth = [], []
    for index in range(frames):
        heartbeat = (index * 2 // fps) % 16  # +1 every 0.5 s
        if index % 45 == 0:
            state = rng.choice(STATES)
            statuses = [rng.choice(COLORS) for _ in range(capacity)]
        app = render_log_solution(state, statuses, scale=tk_scale, origin=(0, 0), heartbeat=heartbeat)
        image = desktop.copy()
        top, left = 140 * canvas[1] // CAPTURE[1], canvas[0] - app.shape[1] - 40
        image[top:top + app.shape[0], left:left + app.shape[1]] = app
        # Scrolling text elsewhere (the ATE program's log) to stress rate control.
        cv2.putText(image, "frame %05d" % index, (100, 600 + (index % 50) * 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        images.append(image)
        truth.append((state, capacity, tuple(statuses), heartbeat_bits(heartbeat)))
    return images, truth


def round_trip(images, bitrate, size, fps=30):
    """Resize to ``size`` (INTER_AREA), encode with libx264 at ``bitrate`` bit/s, decode."""
    width, height = size
    buffer = io.BytesIO()
    container = av.open(buffer, "w", format="mp4")
    stream = container.add_stream("libx264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
    stream.bit_rate = bitrate
    stream.options = {"preset": "veryfast", "tune": "zerolatency", "g": "60"}
    for image in images:
        if (image.shape[1], image.shape[0]) != size:
            image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="bgr24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    buffer.seek(0)
    return [frame.to_ndarray(format="bgr24") for frame in av.open(buffer).decode(video=0)]


def score(decoded, truth):
    reliable = misread = heartbeat_ok = heartbeat_unreadable = heartbeat_misread = 0
    for image, (state, capacity, statuses, heartbeat) in zip(decoded, truth):
        observation = inspect_app_frame(image, 1.0, now=1.0)
        if observation.reliable:
            reliable += 1
            got = observation.state.value if observation.state else None
            if got != state or observation.capacity != capacity or tuple(observation.statuses) != statuses:
                misread += 1
        cells = tuple(observation.heartbeat)
        if len(cells) == 4 and None not in cells:
            if cells == heartbeat:
                heartbeat_ok += 1
            else:
                heartbeat_misread += 1
        else:
            heartbeat_unreadable += 1
    return {"frames": len(truth), "reliable": reliable, "misread": misread, "heartbeat_ok": heartbeat_ok,
            "heartbeat_unreadable": heartbeat_unreadable, "heartbeat_misread": heartbeat_misread}


def parse_size(text):
    width, height = text.lower().split("x")
    return int(width), int(height)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tk-scales", type=float, nargs="+", default=[2.0, 1.0],
                        help="px per Tk unit on the 1920x1080 capture (default: 2 1)")
    parser.add_argument("--sizes", type=parse_size, nargs="+", default=[(1920, 1080), (1280, 720)],
                        help="stream sizes the capture is resized to before encoding (default: 1920x1080 1280x720)")
    parser.add_argument("--mac-scaled", type=int, nargs="*", default=[],
                        help="macOS scaled mode: draw at Tk 2x on a backing store this wide, then downsample to 1920x1080")
    parser.add_argument("--bitrates", type=int, nargs="+", default=[4000, 1500, 800, 400],
                        help="kbit/s (default: 4000 1500 800 400)")
    parser.add_argument("--frames", type=int, default=180, help="frames per case at 30 fps (default: 180)")
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args(argv)

    cases = [(f"Tk {scale:g}x -> {w}x{h}", scale * w / CAPTURE[0], *make_sequence(scale, args.frames), (w, h))
             for scale in args.tk_scales for (w, h) in args.sizes]
    for backing in args.mac_scaled:
        images, truth = make_sequence(2.0, args.frames, canvas=(backing, backing * 9 // 16))
        images = [cv2.resize(image, CAPTURE, interpolation=cv2.INTER_AREA) for image in images]
        cases.append((f"macOS scaled {backing} -> 1920x1080", 2.0 * CAPTURE[0] / backing, images, truth, CAPTURE))

    print(f"display contract {CONTRACT_VERSION}, libx264 zerolatency, {args.frames} frames per case")
    results, failed = [], False
    for label, px_per_tk, images, truth, size in cases:
        for kbps in args.bitrates:
            row = {"case": label, "cell_px": round(10 * px_per_tk, 1), "kbps": kbps,
                   **score(round_trip(images, kbps * 1000, size), truth)}
            failed |= bool(row["misread"] or row["heartbeat_misread"])
            results.append(row)
            print(f"{label:<34} cell {row['cell_px']:>4}px {kbps:>5}kbps  readable {row['reliable']:>3}/{row['frames']}"
                  f"  misread {row['misread']}  heartbeat ok {row['heartbeat_ok']} unreadable "
                  f"{row['heartbeat_unreadable']} misread {row['heartbeat_misread']}", flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    if failed:
        print("FAIL: at least one frame was read as reliable with a wrong result")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
