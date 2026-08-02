"""Still image -> moving clip (Ken Burns).

zoompan rounds pan coordinates to integer output pixels, which reads as visible
jitter. Rendering the pan at `video.supersample` x target resolution and letting
zoompan downscale to the final size divides that jitter by the same factor.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .ffmpeg_utils import ffmpeg
from .progress import Reporter

DIRECTIONS = ("in", "right", "out", "left", "in", "down", "out", "up")
ZOOM_MAX = 1.18


def _kenburns_vf(cfg: Config, direction: str, frames: int) -> str:
    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))
    fps = int(cfg.get("video.fps", 30))
    supersample = max(1, int(cfg.get("video.supersample", 2)))

    big_w, big_h = width * supersample, height * supersample
    span = max(frames - 1, 1)
    step = (ZOOM_MAX - 1.0) / span

    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if direction == "in":
        zoom, x, y = f"min(zoom+{step:.8f},{ZOOM_MAX})", centre_x, centre_y
    elif direction == "out":
        # zoom starts at 1 on the first frame; seed it to ZOOM_MAX then walk down.
        zoom = f"if(lte(zoom,1.0),{ZOOM_MAX},max(1.0,zoom-{step:.8f}))"
        x, y = centre_x, centre_y
    elif direction == "right":
        zoom, x, y = f"{ZOOM_MAX}", f"(iw-iw/zoom)*(on/{span})", centre_y
    elif direction == "left":
        zoom, x, y = f"{ZOOM_MAX}", f"(iw-iw/zoom)*(1-on/{span})", centre_y
    elif direction == "down":
        zoom, x, y = f"{ZOOM_MAX}", centre_x, f"(ih-ih/zoom)*(on/{span})"
    else:  # up
        zoom, x, y = f"{ZOOM_MAX}", centre_x, f"(ih-ih/zoom)*(1-on/{span})"

    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )


def _static_vf(cfg: Config) -> str:
    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},format=yuv420p"
    )


def render_clip(cfg: Config, image: Path, duration: float, dst: Path, index: int) -> Path:
    """Render one image into a clip of exactly `duration` seconds."""
    fps = int(cfg.get("video.fps", 30))
    crf = str(cfg.get("video.crf", 20))
    preset = str(cfg.get("video.preset", "medium"))
    frames = max(2, round(duration * fps))

    common = [
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-frames:v",
        str(frames),
        str(dst),
    ]

    if cfg.get("video.kenburns", True):
        direction = DIRECTIONS[index % len(DIRECTIONS)]
        # A single input frame + zoompan d=frames emits exactly `frames` frames.
        ffmpeg(["-i", str(image), "-vf", _kenburns_vf(cfg, direction, frames), *common])
    else:
        ffmpeg(["-loop", "1", "-i", str(image), "-vf", _static_vf(cfg), *common])

    return dst


def render_all(
    cfg: Config,
    images: list[Path],
    timings: list[dict[str, float]],
    clip_dir: Path,
    reporter: Reporter | None = None,
) -> tuple[list[Path], list[float]]:
    """Render every scene clip, returning the clips and their exact durations.

    Each clip runs for its scene's narration + the inter-scene gap. With an xfade
    transition, clips are extended by the transition length so the overlap eats
    the padding rather than the content.
    """
    reporter = reporter or Reporter()
    clip_dir.mkdir(parents=True, exist_ok=True)
    use_xfade = str(cfg.get("video.transition", "xfade")).lower() == "xfade"
    transition = float(cfg.get("video.transition_seconds", 0.6)) if use_xfade else 0.0

    fps = int(cfg.get("video.fps", 30))
    clips: list[Path] = []
    durations: list[float] = []
    total = len(images)

    for i, (image, timing) in enumerate(zip(images, timings)):
        reporter.substep(i, total, f"clip {i + 1}/{total}")
        wanted = timing["duration"] + timing.get("gap", 0.0) + transition
        # Quantise to whole frames so the durations we hand to the xfade
        # offset chain are exactly what got encoded — otherwise sub-frame
        # rounding accumulates and the last scene gets clipped.
        frames = max(2, round(wanted * fps))
        exact = frames / fps

        dst = clip_dir / f"clip_{i:03d}.mp4"
        if dst.exists() and dst.stat().st_size > 4096:
            reporter.log(f"clip {i + 1}/{total} cached")
        else:
            reporter.log(f"clip {i + 1}/{total} rendering ({exact:.1f}s)")
            render_clip(cfg, image, exact, dst, i)
        clips.append(dst)
        durations.append(exact)

    return clips, durations
