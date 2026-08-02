"""Thumbnail: generated background + heavy outlined text overlay."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from . import fonts, visuals
from .config import Config


def render(cfg: Config, plan: dict[str, Any], dst: Path) -> Path | None:
    """Draw a 1280x720 thumbnail. Returns None if disabled or unrenderable."""
    if not cfg.get("thumbnail.enabled", True):
        return None

    from PIL import Image, ImageDraw, ImageEnhance

    width, height = 1280, 720
    background = dst.parent / "thumbnail_bg.png"

    if not background.exists():
        prompt = plan.get("thumbnail_prompt") or plan.get("topic", "")
        source = str(cfg.get("visuals.source", "ai")).lower()
        try:
            visuals.generate(cfg, source, prompt, background)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! thumbnail background failed ({exc}); using a gradient")
            visuals.generate(cfg, "gradient", prompt, background)

    image = Image.open(background).convert("RGB")

    # Cover-crop to 16:9.
    scale = max(width / image.width, height / image.height)
    image = image.resize(
        (max(width, int(image.width * scale)), max(height, int(image.height * scale))),
        Image.LANCZOS,
    )
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height))

    image = ImageEnhance.Color(image).enhance(1.22)
    image = ImageEnhance.Contrast(image).enhance(1.10)

    # Darken the lower band so text always reads. Alpha must be strongest at the
    # very bottom and ease to zero at mid-height, or the ramp leaves a hard seam.
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade = ImageDraw.Draw(overlay)
    band = height // 2
    for i in range(band):
        y = height - 1 - i
        t = i / band  # 0 at the bottom row, 1 at mid-height
        alpha = int(215 * (1.0 - t) ** 1.3)
        shade.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    text = (plan.get("thumbnail_text") or plan.get("title", "")).strip().upper()
    if text:
        draw = ImageDraw.Draw(image)
        lines = textwrap.wrap(text, width=16)[:3]

        size = 132 if len(lines) <= 2 else 104
        font = fonts.load(size)

        # Shrink until the widest line fits with margins.
        for _ in range(14):
            widest = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
            if widest <= width - 130 or size <= 44:
                break
            size -= 8
            font = fonts.load(size)

        line_height = int(size * 1.14)
        block = line_height * len(lines)
        y = height - block - 56

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (width - (bbox[2] - bbox[0])) // 2
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255),
                stroke_width=max(6, size // 16),
                stroke_fill=(8, 8, 8),
            )
            y += line_height

    image.save(dst, "JPEG", quality=90, optimize=True)
    return dst
