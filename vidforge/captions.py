"""Burned-in captions.

Word timings come from Whisper per scene (accurate) or from a length-weighted
split across the scene's measured duration (free, and surprisingly decent).

Two renderers produce the burn-in:

  ass      — an ASS subtitle file for ffmpeg's `subtitles` filter. Cheapest, but
             needs an ffmpeg built with libass.
  overlay  — Pillow-drawn transparent PNGs fed through the concat demuxer into a
             single `overlay`. Works on any ffmpeg build, including Homebrew's
             default bottle which has neither libass nor libfreetype.

`captions.renderer: auto` picks `ass` when the filter exists and `overlay`
otherwise, so the same config works on any machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fonts
from .config import Config, require_key
from .ffmpeg_utils import has_filter

HIGHLIGHT = (255, 200, 60)
PLAIN = (255, 255, 255)
STROKE = (10, 10, 10)

_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=require_key("OPENAI_API_KEY", "caption alignment"))
    return _client


@dataclass
class CaptionTrack:
    """What assemble.render needs in order to burn the captions in."""

    mode: str                       # "ass" | "overlay"
    path: Path                      # .ass file, or the concat list
    width: int = 0
    height: int = 0
    margin_bottom: int = 0


# --------------------------------------------------------------------------
# Word timing
# --------------------------------------------------------------------------


def _words_via_whisper(audio: Path) -> list[dict[str, Any]] | None:
    try:
        with audio.open("rb") as fh:
            result = _openai().audio.transcriptions.create(
                model="whisper-1",
                file=fh,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
    except Exception as exc:  # noqa: BLE001 - alignment is best-effort
        print(f"   whisper alignment unavailable ({exc}); estimating timings")
        return None

    out = []
    for w in getattr(result, "words", None) or []:
        text = (getattr(w, "word", None) or "").strip()
        if text:
            out.append(
                {
                    "text": text,
                    "start": float(getattr(w, "start", 0.0)),
                    "end": float(getattr(w, "end", 0.0)),
                }
            )
    return out or None


def _words_estimated(narration: str, duration: float) -> list[dict[str, Any]]:
    tokens = narration.split()
    if not tokens:
        return []
    weights = [max(len(t), 2) for t in tokens]
    total = sum(weights)

    words = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * (weight / total)
        words.append({"text": token, "start": cursor, "end": cursor + span})
        cursor += span
    return words


def scene_words(
    cfg: Config, scene: dict[str, Any], audio: Path, timing: dict[str, float]
) -> list[dict[str, Any]]:
    mode = str(cfg.get("captions.align", "whisper")).lower()
    words = _words_via_whisper(audio) if mode == "whisper" else None
    if not words:
        words = _words_estimated(scene["narration"], timing["duration"])

    offset = timing["start"]
    return [
        {"text": w["text"], "start": w["start"] + offset, "end": w["end"] + offset}
        for w in words
    ]


SENTENCE_END = (".", "!", "?", ":", "…")


def group(words: list[dict[str, Any]], per_cue: int) -> list[list[dict[str, Any]]]:
    """Chunk words into cues, breaking at sentence ends.

    Without the sentence break a cue reads like "for months But" — the start of
    the next sentence gets stranded alongside the tail of the previous one.
    """
    per_cue = max(1, per_cue)
    cues: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for word in words:
        current.append(word)
        ends_sentence = word["text"].rstrip('"\')]').endswith(SENTENCE_END)
        if len(current) >= per_cue or ends_sentence:
            cues.append(current)
            current = []

    if current:
        cues.append(current)
    return cues


# --------------------------------------------------------------------------
# Renderer: ASS
# --------------------------------------------------------------------------


def _ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _build_ass(cfg: Config, cues: list[list[dict[str, Any]]], dst: Path) -> CaptionTrack:
    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))
    font = cfg.get("captions.font", "Arial")
    size = int(cfg.get("captions.font_size", 84))
    karaoke = bool(cfg.get("captions.karaoke", True))
    margin_v = int(height * 0.11)

    # ASS colours are &HAABBGGRR. Secondary is the pre-highlight karaoke colour.
    lines = [
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{font},{size},&H00FFFFFF,&H003CC8FF,&H00101010,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,5,3,2,140,140,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    ]

    for cue in cues:
        start, end = cue[0]["start"], max(cue[-1]["end"], cue[0]["start"] + 0.2)
        if karaoke:
            text = " ".join(
                f"{{\\k{max(1, round((w['end'] - w['start']) * 100))}}}{_ass_escape(w['text'])}"
                for w in cue
            )
        else:
            text = _ass_escape(" ".join(w["text"] for w in cue))
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Main,,0,0,0,,{text}\n"
        )

    dst.write_text("".join(lines), encoding="utf-8")
    return CaptionTrack(mode="ass", path=dst)


# --------------------------------------------------------------------------
# Renderer: PNG overlay
# --------------------------------------------------------------------------


def _draw_cue(
    cue: list[dict[str, Any]],
    active: int | None,
    size: tuple[int, int],
    font,
    dst: Path,
) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    gap = max(8, font.size // 5 if hasattr(font, "size") else 14)
    widths = [draw.textbbox((0, 0), w["text"], font=font)[2] for w in cue]
    total = sum(widths) + gap * (len(cue) - 1)

    x = (size[0] - total) // 2
    y = (size[1] - (font.size if hasattr(font, "size") else 60)) // 2

    stroke = max(4, (font.size if hasattr(font, "size") else 60) // 14)
    for i, word in enumerate(cue):
        colour = HIGHLIGHT if (active is not None and i == active) else PLAIN
        draw.text(
            (x, y),
            word["text"],
            font=font,
            fill=colour,
            stroke_width=stroke,
            stroke_fill=STROKE,
        )
        x += widths[i] + gap

    image.save(dst, "PNG")


def _build_overlay(
    cfg: Config, cues: list[list[dict[str, Any]]], out_dir: Path
) -> CaptionTrack:
    from PIL import Image

    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))
    size_pt = int(cfg.get("captions.font_size", 84))
    karaoke = bool(cfg.get("captions.karaoke", True))
    margin = int(height * 0.09)

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("cue_*.png"):
        stale.unlink()

    font = fonts.load(size_pt, preferred=str(cfg.get("captions.font", "Arial")))
    band = (width, int(size_pt * 2.0))

    blank = out_dir / "cue_blank.png"
    Image.new("RGBA", band, (0, 0, 0, 0)).save(blank, "PNG")

    # (image, duration) pairs, with transparent filler wherever nothing is spoken.
    entries: list[tuple[Path, float]] = []
    cursor = 0.0
    index = 0

    def hold(path: Path, until: float) -> None:
        nonlocal cursor
        span = until - cursor
        if span > 0.02:
            entries.append((path, span))
            cursor = until

    for cue in cues:
        hold(blank, cue[0]["start"])

        if karaoke:
            for i, word in enumerate(cue):
                frame = out_dir / f"cue_{index:05d}.png"
                _draw_cue(cue, i, band, font, frame)
                index += 1
                hold(frame, max(word["end"], cursor + 0.04))
        else:
            frame = out_dir / f"cue_{index:05d}.png"
            _draw_cue(cue, None, band, font, frame)
            index += 1
            hold(frame, max(cue[-1]["end"], cursor + 0.1))

    # Pad well past the end of the narration. assemble.render overlays with
    # shortest=1, so the caption stream must outlast the video, never the other
    # way round — a short caption stream would truncate the final scene.
    entries.append((blank, 10.0))

    concat = out_dir / "captions.txt"
    lines = ["ffconcat version 1.0\n"]
    for path, duration in entries:
        lines.append(f"file '{path.resolve()}'\n")
        lines.append(f"duration {duration:.3f}\n")
    lines.append(f"file '{entries[-1][0].resolve()}'\n")
    concat.write_text("".join(lines), encoding="utf-8")

    return CaptionTrack(
        mode="overlay",
        path=concat,
        width=band[0],
        height=band[1],
        margin_bottom=margin,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def choose_renderer(cfg: Config) -> str:
    requested = str(cfg.get("captions.renderer", "auto")).lower()
    if requested in ("ass", "overlay"):
        return requested
    return "ass" if has_filter("subtitles") else "overlay"


def build(cfg: Config, words: list[dict[str, Any]], work_dir: Path) -> CaptionTrack | None:
    if not words:
        return None

    cues = group(words, int(cfg.get("captions.words_per_cue", 3)))
    renderer = choose_renderer(cfg)

    if renderer == "ass":
        return _build_ass(cfg, cues, work_dir / "captions.ass")

    if not fonts.available():
        print("   ! no usable TrueType font found; skipping captions")
        return None
    return _build_overlay(cfg, cues, work_dir / "captions")
