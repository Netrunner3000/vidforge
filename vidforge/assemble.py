"""Final mux: clips + transitions + captions + narration + music -> one mp4.

Video is stitched in a single filter_complex pass so subtitles are burned during
the same encode rather than costing a second generation loss.
"""

from __future__ import annotations

import random
from pathlib import Path

from typing import TYPE_CHECKING

from .config import MUSIC_DIR, Config
from .ffmpeg_utils import ffmpeg, ffmpeg_bin, has_filter, probe_duration, run

if TYPE_CHECKING:
    from .captions import CaptionTrack
    from .progress import Reporter

MUSIC_EXTS = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------


def pick_music() -> Path | None:
    if not MUSIC_DIR.exists():
        return None
    tracks = [p for p in sorted(MUSIC_DIR.iterdir()) if p.suffix.lower() in MUSIC_EXTS]
    return random.choice(tracks) if tracks else None


def mix_audio(
    cfg: Config, narration: Path, dst: Path, reporter: "Reporter | None" = None
) -> Path:
    """Lay a looped music bed under the narration and normalise loudness.

    Two things matter for YouTube here:

    * `amix` defaults to `normalize=1`, which divides every input by the input
      count — that alone drops the narration ~6 dB the moment music is added.
    * YouTube attenuates audio louder than roughly -14 LUFS but never boosts
      quiet audio, so an un-normalised video just plays quieter than everything
      around it. `loudnorm` pins the master to the target.
    """
    log = reporter.log if reporter else (lambda message: print(f"   {message}"))
    duration = probe_duration(narration)
    normalize = bool(cfg.get("audio.normalize", True))
    target = float(cfg.get("audio.loudness_lufs", -14.0))
    peak = float(cfg.get("audio.true_peak_db", -1.5))
    loudnorm = f",loudnorm=I={target}:TP={peak}:LRA=11" if normalize else ""

    track = pick_music() if cfg.get("music.enabled", True) else None
    inputs = ["-i", str(narration)]

    if track is None:
        if not normalize:
            return narration
        log("no music bed — normalising narration only")
        graph = f"[0:a]aresample=48000{loudnorm}[out]"
    else:
        log(f"mixing music bed: {track.name}")
        inputs += ["-stream_loop", "-1", "-i", str(track)]
        volume = float(cfg.get("music.volume", 0.13))
        fade_out_at = max(duration - 3, 0)
        duck = bool(cfg.get("music.duck", True)) and has_filter("sidechaincompress")

        music = (
            f"[1:a]aresample=48000,volume={volume},afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_at:.2f}:d=3[music];"
        )
        if duck:
            # One narration copy keys the compressor, the other gets mixed in.
            graph = (
                music
                + "[0:a]aresample=48000,asplit=2[narr_mix][narr_key];"
                "[music][narr_key]sidechaincompress="
                "threshold=0.03:ratio=12:attack=15:release=350[ducked];"
                "[narr_mix][ducked]amix=inputs=2:duration=first"
                f":dropout_transition=0:normalize=0{loudnorm}[out]"
            )
        else:
            graph = (
                music
                + "[0:a]aresample=48000[narr];"
                "[narr][music]amix=inputs=2:duration=first"
                f":dropout_transition=0:normalize=0{loudnorm}[out]"
            )

    ffmpeg(
        [
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-t",
            f"{duration:.3f}",
            "-ar",
            "48000",  # loudnorm resamples internally; force it back
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    return dst


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------


def expected_duration(cfg: Config, durations: list[float]) -> float:
    """How long the joined video should be, given per-clip lengths."""
    total = sum(durations)
    if len(durations) > 1 and str(cfg.get("video.transition", "xfade")).lower() == "xfade":
        total -= (len(durations) - 1) * float(cfg.get("video.transition_seconds", 0.6))
    return total


def _video_graph(cfg: Config, clips: list[Path], durations: list[float]) -> tuple[str, str]:
    """Build the filter_complex that joins clips, returning (graph, out_label)."""
    if len(clips) == 1:
        return "[0:v]null[vjoin]", "vjoin"

    if str(cfg.get("video.transition", "xfade")).lower() != "xfade":
        streams = "".join(f"[{i}:v]" for i in range(len(clips)))
        return f"{streams}concat=n={len(clips)}:v=1:a=0[vjoin]", "vjoin"

    transition = float(cfg.get("video.transition_seconds", 0.6))
    parts: list[str] = []
    prev = "0:v"
    offset = 0.0

    for i in range(1, len(clips)):
        offset += durations[i - 1] - transition
        label = f"vx{i}"
        parts.append(
            f"[{prev}][{i}:v]xfade=transition=fade:"
            f"duration={transition:.3f}:offset={offset:.3f}[{label}]"
        )
        prev = label

    return ";".join(parts), prev


def render(
    cfg: Config,
    clips: list[Path],
    durations: list[float],
    audio: Path,
    dst: Path,
    *,
    captions: "CaptionTrack | None" = None,
    work_dir: Path | None = None,
) -> Path:
    """Join clips, burn captions, attach audio — one encode pass."""
    work_dir = work_dir or dst.parent
    graph, label = _video_graph(cfg, clips, durations)

    args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
    for clip in clips:
        args += ["-i", str(clip.resolve())]

    caption_index: int | None = None
    if captions is not None and captions.mode == "overlay":
        caption_index = len(clips)
        args += ["-f", "concat", "-safe", "0", "-i", str(captions.path.resolve())]

    audio_index = len(clips) + (1 if caption_index is not None else 0)
    args += ["-i", str(audio.resolve())]

    if captions is not None:
        if captions.mode == "ass":
            # Run ffmpeg with cwd = the subtitle's folder and reference the bare
            # filename, sidestepping the subtitles filter's path-escaping rules.
            work_dir = captions.path.parent
            graph = f"{graph};[{label}]subtitles={captions.path.name}[vout]"
            label = "vout"
        else:
            # shortest=1 keeps the caption stream (which is deliberately padded
            # past the end of the narration) from extending the video.
            graph = (
                f"{graph};[{caption_index}:v]format=rgba[cap];"
                f"[{label}][cap]overlay=x=(W-w)/2:y=H-h-{captions.margin_bottom}"
                f":format=auto:shortest=1:eof_action=pass,format=yuv420p[vout]"
            )
            label = "vout"

    args += [
        "-filter_complex",
        graph,
        "-map",
        f"[{label}]",
        "-map",
        f"{audio_index}:a",
        "-t",
        f"{expected_duration(cfg, durations):.3f}",
        "-c:v",
        "libx264",
        "-crf",
        str(cfg.get("video.crf", 20)),
        "-preset",
        str(cfg.get("video.preset", "medium")),
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(cfg.get("video.fps", 30)),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(dst.resolve()),
    ]

    run(args, cwd=work_dir)
    return dst
