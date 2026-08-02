"""The end-to-end run: topic -> mp4 + thumbnail + metadata.

Every stage writes into the video's own directory and records itself in
manifest.json, so re-running a crashed job resumes instead of starting over.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    assemble,
    captions,
    history,
    ideation,
    metadata,
    motion,
    script,
    thumbnail,
    visuals,
    voice,
)
from .config import Config, output_root
from .ffmpeg_utils import probe_duration
from .progress import ConsoleReporter, Reporter


@dataclass
class Build:
    slug: str
    root: Path
    plan: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def image_dir(self) -> Path:
        return self.root / "images"

    @property
    def clip_dir(self) -> Path:
        return self.root / "clips"

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def video_path(self) -> Path:
        return self.root / f"{self.slug}.mp4"

    @property
    def thumbnail_path(self) -> Path:
        return self.root / "thumbnail.jpg"

    @property
    def metadata_path(self) -> Path:
        return self.root / "metadata.json"

    def save_manifest(self) -> None:
        (self.root / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def mark(self, stage: str, **extra: Any) -> None:
        self.manifest.setdefault("stages", {})[stage] = {
            "done_at": history.utcnow(),
            **extra,
        }
        self.save_manifest()


def _load_or_create(slug: str | None, plan: dict[str, Any] | None) -> Build:
    if slug:
        root = output_root() / slug
        if not root.exists():
            raise FileNotFoundError(f"no build directory at {root}")
        build = Build(slug=slug, root=root)
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            build.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_path = root / "script.json"
        if plan_path.exists():
            build.plan = json.loads(plan_path.read_text(encoding="utf-8"))
        return build

    assert plan is not None
    slug = history.new_slug(plan["title"])
    root = output_root() / slug
    root.mkdir(parents=True, exist_ok=True)
    return Build(slug=slug, root=root, plan=plan)


def produce(
    cfg: Config,
    *,
    topic: str | None = None,
    resume_slug: str | None = None,
    reporter: Reporter | None = None,
) -> Build:
    """Produce one complete video. Returns the finished Build."""
    started = time.time()
    reporter = reporter or ConsoleReporter()

    # ---------------------------------------------------------------- script
    reporter.begin("script")
    if resume_slug:
        build = _load_or_create(resume_slug, None)
        if not build.plan:
            raise RuntimeError(f"{resume_slug} has no script.json to resume from")
        reporter.log(f"resuming {build.slug}")
        reporter.log(f"title: {build.plan['title']}")
    else:
        chosen = ideation.next_topic(cfg, topic)
        reporter.log(f"topic: {chosen}")
        plan = script.plan(cfg, chosen)
        build = _load_or_create(None, plan)
        (build.root / "script.json").write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        build.manifest = {
            "slug": build.slug,
            "topic": chosen,
            "title": plan["title"],
            "created_at": history.utcnow(),
            "config": {
                "script_provider": cfg.get("script.provider"),
                "visuals_source": cfg.get("visuals.source"),
                "voice": cfg.get("voice.voice"),
            },
            "stages": {},
        }
        build.mark("script", scenes=len(plan["scenes"]), words=plan["word_count"])
        # Record it now, not just on success — a run that is cancelled or dies
        # partway still needs to show up in the library so it can be resumed.
        history.record(
            {
                "slug": build.slug,
                "topic": chosen,
                "title": plan["title"],
                "created": history.utcnow(),
                "duration_seconds": 0,
                "path": str(build.video_path),
                "complete": False,
                "published": False,
            }
        )
        reporter.log(
            f"{len(plan['scenes'])} scenes, {plan['word_count']} words "
            f"(~{script.estimate_seconds(plan) / 60:.1f} min)"
        )
        reporter.log(f"title: {plan['title']}")

    scenes = build.plan["scenes"]
    for path in (build.audio_dir, build.image_dir, build.clip_dir, build.work_dir):
        path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- narration
    reporter.begin("voice")
    scene_audio = voice.render_scenes(cfg, scenes, build.audio_dir, reporter)
    narration, timings = voice.build_track(cfg, scene_audio, build.work_dir)
    narration_seconds = probe_duration(narration)
    build.manifest["timings"] = timings
    build.mark("voice", seconds=round(narration_seconds, 2))
    reporter.log(f"narration track: {narration_seconds / 60:.1f} min")

    # -------------------------------------------------------------- captions
    caption_track = None
    if cfg.get("captions.enabled", True):
        renderer = captions.choose_renderer(cfg)
        reporter.begin("captions", f"({renderer} renderer)")
        words_path = build.work_dir / "words.json"

        if words_path.exists():
            words = json.loads(words_path.read_text(encoding="utf-8"))
            reporter.log(f"{len(words)} word timings cached")
        else:
            words = []
            for i, (scene, audio, timing) in enumerate(
                zip(scenes, scene_audio, timings)
            ):
                reporter.substep(i, len(scenes), f"scene {i + 1}/{len(scenes)}")
                words.extend(captions.scene_words(cfg, scene, audio, timing))
            words_path.write_text(json.dumps(words), encoding="utf-8")
            reporter.log(f"{len(words)} words timed")

        caption_track = captions.build(cfg, words, build.work_dir)
        build.mark("captions", words=len(words), renderer=renderer)

    # --------------------------------------------------------------- visuals
    reporter.begin("visuals")
    images = visuals.render_scenes(cfg, scenes, build.image_dir, reporter)
    build.mark("visuals", images=len(images))

    # ---------------------------------------------------------------- motion
    reporter.begin("motion")
    clips, durations = motion.render_all(cfg, images, timings, build.clip_dir, reporter)
    build.mark("motion", clips=len(clips))

    # ----------------------------------------------------------------- audio
    reporter.begin("audio")
    mixed = assemble.mix_audio(
        cfg, narration, build.work_dir / "final_audio.wav", reporter
    )

    # ------------------------------------------------------------- final mux
    reporter.begin("assemble")
    assemble.render(
        cfg,
        clips,
        durations,
        mixed,
        build.video_path,
        captions=caption_track,
        work_dir=build.work_dir,
    )
    duration = probe_duration(build.video_path)
    size_mb = build.video_path.stat().st_size / 1_048_576
    build.mark("assemble", seconds=round(duration, 2), size_mb=round(size_mb, 1))
    reporter.log(f"{build.video_path.name} — {duration / 60:.1f} min, {size_mb:.0f} MB")

    # ------------------------------------------------------------- thumbnail
    if cfg.get("thumbnail.enabled", True):
        reporter.begin("thumbnail")
        try:
            thumbnail.render(cfg, build.plan, build.thumbnail_path)
            build.mark("thumbnail")
        except Exception as exc:  # noqa: BLE001 - never fail a render over this
            reporter.log(f"! thumbnail failed ({exc}); video is fine")

    # -------------------------------------------------------------- metadata
    meta = metadata.build(build.plan, timings, duration=duration)
    metadata.write(meta, build.metadata_path)
    build.mark("metadata")

    # --------------------------------------------------------------- history
    elapsed = time.time() - started
    build.manifest["render_seconds"] = round(elapsed, 1)
    build.manifest["estimated_cost_usd"] = script.estimate_cost_usd(cfg, build.plan)
    build.save_manifest()

    history.record(
        {
            "slug": build.slug,
            "topic": build.plan.get("topic"),
            "title": build.plan.get("title"),
            "created": history.utcnow(),
            "duration_seconds": round(duration, 2),
            "path": str(build.video_path),
            "complete": True,
        }
    )

    cost = build.manifest["estimated_cost_usd"]["total"]
    reporter.log(f"done in {elapsed / 60:.1f} min — est. API cost ${cost:.2f}")
    reporter.log(str(build.root))
    return build
