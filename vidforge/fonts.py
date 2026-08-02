"""Locating a usable TrueType face.

Both the caption renderer and the thumbnail draw text with Pillow, because many
ffmpeg builds (including Homebrew's default bottle) ship without libass or
libfreetype, so neither `subtitles` nor `drawtext` can be relied on.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .config import FONTS_DIR

SYSTEM_FONT_DIRS = (
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
)

# Heaviest faces first — captions and thumbnails both want maximum weight.
BOLD_NAMES = (
    "Arial Black.ttf",
    "Arial Bold.ttf",
    "Arial Unicode.ttf",
    "Helvetica.ttc",
    "HelveticaNeue.ttc",
    "Impact.ttf",
    "Verdana Bold.ttf",
    "Tahoma Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "Arial.ttf",
    "Supplemental/Arial.ttf",
)


@lru_cache(maxsize=1)
def _candidates() -> tuple[Path, ...]:
    found: list[Path] = []

    if FONTS_DIR.exists():
        for pattern in ("*.ttf", "*.ttc", "*.otf"):
            found.extend(sorted(FONTS_DIR.glob(pattern)))

    for directory in SYSTEM_FONT_DIRS:
        if not directory.exists():
            continue
        for name in BOLD_NAMES:
            path = directory / name
            if path.exists():
                found.append(path)

    return tuple(found)


@lru_cache(maxsize=64)
def load(size: int, *, preferred: str | None = None):
    """Return a Pillow font at `size`, falling back to the bitmap default."""
    from PIL import ImageFont

    ordered = list(_candidates())
    if preferred:
        needle = preferred.lower().replace(" ", "")
        ordered.sort(key=lambda p: needle not in p.stem.lower().replace(" ", ""))

    for path in ordered:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue

    return ImageFont.load_default()


def available() -> bool:
    return bool(_candidates())
