#!/usr/bin/env python3
"""Draw assets/icon.icns for the .app bundle. Run via build_app.sh."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
SIZES = (16, 32, 64, 128, 256, 512, 1024)


def draw(size: int) -> Image.Image:
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_ctx = ImageDraw.Draw(image)

    # Vertical gradient, masked to a rounded square. Drawing two stacked
    # rectangles instead leaves a hard seam where they meet.
    top, bottom = (86, 146, 253), (37, 90, 214)
    gradient = Image.new("RGB", (1, size))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / max(size - 1, 1)
        grad_draw.point(
            (0, y),
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(220 * scale), fill=255
    )
    image.paste(gradient, (0, 0), mask)
    draw_ctx = ImageDraw.Draw(image)

    # Play triangle.
    cx, cy = size * 0.5, size * 0.5
    reach = size * 0.20
    draw_ctx.polygon(
        [
            (cx - reach * 0.75, cy - reach),
            (cx - reach * 0.75, cy + reach),
            (cx + reach, cy),
        ],
        fill=(255, 255, 255, 255),
    )

    # Sprocket holes down each edge, so it reads as film at a glance.
    hole_w, hole_h = size * 0.070, size * 0.048
    for i in range(4):
        y = size * (0.20 + i * 0.20)
        for x in (size * 0.055, size - size * 0.055 - hole_w):
            draw_ctx.rounded_rectangle(
                [x, y, x + hole_w, y + hole_h],
                radius=max(1, int(14 * scale)),
                fill=(255, 255, 255, 90),
            )

    return image


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    iconset = ASSETS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()

    for size in SIZES:
        draw(size).save(iconset / f"icon_{size}x{size}.png")
        if size <= 512:
            draw(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")

    draw(1024).save(ASSETS / "icon.png")

    if shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")],
            check=True,
        )
        shutil.rmtree(iconset)
        print(f"wrote {ASSETS / 'icon.icns'}")
    else:
        print("iconutil not found (macOS only) — wrote assets/icon.png only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
