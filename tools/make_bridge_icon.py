#!/usr/bin/env python3
"""Build the bridge icon (issue #115) from its 16×16 ASCII drawing.

Input : tools/bridge_icon_16x16.txt   (# blue, w white, g green, o orange, . transparent)
Output: host-app/assets/bridge_16.png, bridge_32.png, bridge_48.png, bridge_256.png, bridge.ico

Scaling is nearest-neighbour, so every pixel of the 16×16 drawing stays a sharp square at each size.
Run it again after editing the drawing; the outputs are committed, so the build never needs Pillow for this.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "bridge_icon_16x16.txt"
OUT_DIR = ROOT / "host-app" / "assets"
SIZES = (16, 32, 48, 256)

PALETTE = {
    "#": (0x00, 0x6D, 0xB6, 255),  # brand blue background
    "w": (0xFF, 0xFF, 0xFF, 255),  # bridge arch and deck
    "g": (0x4C, 0xAF, 0x50, 255),  # host (upper computer) side
    "o": (0xFF, 0x98, 0x00, 255),  # ATE side
    ".": (0, 0, 0, 0),             # transparent corners
}


def load_grid(path: Path = SOURCE) -> list[str]:
    rows = [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 16 or any(len(r) != 16 for r in rows):
        raise SystemExit(f"{path.name}: expected 16 rows of 16 characters")
    unknown = {c for r in rows for c in r} - set(PALETTE)
    if unknown:
        raise SystemExit(f"{path.name}: unknown symbols {sorted(unknown)}")
    return rows


def render(rows: list[str], size: int) -> Image.Image:
    base = Image.new("RGBA", (16, 16))
    base.putdata([PALETTE[c] for r in rows for c in r])
    return base.resize((size, size), Image.Resampling.NEAREST)


def main() -> None:
    rows = load_grid()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    images = {size: render(rows, size) for size in SIZES}
    for size, image in images.items():
        image.save(OUT_DIR / f"bridge_{size}.png", optimize=True)
    # Pillow resamples the 256 px master down to each ICO size; 256 is an exact 16× multiple of 16,
    # so every block stays a solid square.
    images[256].save(OUT_DIR / "bridge.ico", format="ICO", sizes=[(s, s) for s in (16, 32, 48, 256)])
    print("wrote", ", ".join(f"bridge_{s}.png" for s in SIZES), "and bridge.ico")


if __name__ == "__main__":
    main()
