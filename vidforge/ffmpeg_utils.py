"""Thin wrappers over ffmpeg/ffprobe.

Homebrew ffmpeg is preferred over anything else on PATH, matching the rest of
the lab (macOS ships tools that are not the real thing).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

_BREW_PREFIX = Path("/opt/homebrew/bin")


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def _binary(name: str) -> str:
    brew = _BREW_PREFIX / name
    if brew.exists():
        return str(brew)
    found = shutil.which(name)
    if not found:
        raise FFmpegError(
            f"{name} not found. Install it with:  brew install ffmpeg"
        )
    return found


def ffmpeg_bin() -> str:
    return _binary("ffmpeg")


def ffprobe_bin() -> str:
    return _binary("ffprobe")


def run(args: list[str], *, cwd: Path | None = None, quiet: bool = True) -> None:
    """Run an ffmpeg-family command, raising with the tail of stderr on failure."""
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-25:])
        raise FFmpegError(
            f"command failed ({proc.returncode}): {' '.join(args[:3])} ...\n{tail}"
        )
    if not quiet and proc.stderr:
        print(proc.stderr)


def ffmpeg(args: list[str], *, cwd: Path | None = None) -> None:
    run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd)


def probe_duration(path: Path) -> float:
    """Duration of a media file in seconds."""
    proc = subprocess.run(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path.name}: {proc.stderr.strip()}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise FFmpegError(f"could not read duration of {path.name}: {exc}") from exc


@lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True
    )
    return f" {name} " in proc.stdout


def make_silence(path: Path, seconds: float, *, sample_rate: int = 48000) -> Path:
    """Render a silent stereo WAV of the given length."""
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
            "-t",
            f"{seconds:.4f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def to_wav(src: Path, dst: Path, *, sample_rate: int = 48000) -> Path:
    """Normalise any audio file to 48k stereo PCM so concat is exact."""
    ffmpeg(
        [
            "-i",
            str(src),
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    return dst


def concat_demux(parts: list[Path], dst: Path, work_dir: Path) -> Path:
    """Concatenate identically-formatted files with the concat demuxer."""
    list_path = work_dir / f"{dst.stem}_concat.txt"
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(dst)]
    )
    return dst
