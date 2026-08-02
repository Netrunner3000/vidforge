"""Narration: one TTS clip per scene, then a single narration track.

Per-scene clips are cached on disk, so a re-run after a crash only regenerates
what's missing.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from .config import Config, require_key
from .ffmpeg_utils import concat_demux, make_silence, probe_duration, to_wav
from .progress import Reporter

RETRIES = 3
_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=require_key("OPENAI_API_KEY", "text-to-speech"))
    return _client


def _speak(cfg: Config, text: str, dst: Path) -> None:
    kwargs: dict[str, Any] = {
        "model": cfg.get("voice.model", "gpt-4o-mini-tts"),
        "voice": cfg.get("voice.voice", "onyx"),
        "input": text,
    }
    instructions = cfg.get("voice.instructions")
    if instructions:
        kwargs["instructions"] = " ".join(str(instructions).split())

    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with _openai().audio.speech.with_streaming_response.create(**kwargs) as resp:
                resp.stream_to_file(str(dst))
            return
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if (
                "insufficient_quota" in message
                or "exceeded your current quota" in message
                or "Billing hard limit" in message
                or "billing_hard_limit_reached" in message
            ):
                raise RuntimeError(
                    "OpenAI billing limit reached — raise the cap or top up at "
                    "platform.openai.com/settings/organization/limits, then re-run. "
                    "Finished scenes are cached and will be skipped."
                ) from exc
            last = exc
            if attempt < RETRIES:
                time.sleep(2 * attempt + random.random())

    raise RuntimeError(f"TTS failed for a scene after {RETRIES} attempts: {last}")


def render_scenes(
    cfg: Config,
    scenes: list[dict[str, Any]],
    audio_dir: Path,
    reporter: Reporter | None = None,
) -> list[Path]:
    """Generate (or reuse) one mp3 per scene."""
    reporter = reporter or Reporter()
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    total = len(scenes)

    for scene in scenes:
        reporter.substep(scene["index"], total, f"scene {scene['index'] + 1}/{total}")
        dst = audio_dir / f"scene_{scene['index']:03d}.mp3"
        if dst.exists() and dst.stat().st_size > 1024:
            reporter.log(f"scene {scene['index'] + 1}/{total} narration cached")
        else:
            reporter.log(f"scene {scene['index'] + 1}/{total} narrating")
            _speak(cfg, scene["narration"], dst)
        paths.append(dst)

    return paths


def build_track(
    cfg: Config, scene_audio: list[Path], work_dir: Path
) -> tuple[Path, list[dict[str, float]]]:
    """Join scene clips into one narration WAV, returning per-scene timings.

    Timings are what everything downstream is cut against: captions are offset by
    `start`, and each video clip is built to `duration + gap` long.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    gap = float(cfg.get("voice.scene_gap", 0.35))

    silence = None
    if gap > 0:
        silence = make_silence(work_dir / "gap.wav", gap)

    parts: list[Path] = []
    timings: list[dict[str, float]] = []
    cursor = 0.0

    for i, mp3 in enumerate(scene_audio):
        wav = to_wav(mp3, work_dir / f"scene_{i:03d}.wav")
        duration = probe_duration(wav)
        timings.append({"index": i, "start": cursor, "duration": duration, "gap": gap})
        parts.append(wav)
        cursor += duration
        if silence is not None:
            parts.append(silence)
            cursor += gap

    track = concat_demux(parts, work_dir / "narration.wav", work_dir)
    return track, timings
