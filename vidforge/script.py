"""Topic -> a fully structured video script.

One LLM call produces narration, per-scene image prompts, and all the YouTube
metadata, so the whole video is internally consistent.
"""

from __future__ import annotations

import math
from typing import Any

from .config import Config
from .llm import complete_json

SCRIPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "description",
        "tags",
        "scenes",
        "thumbnail_prompt",
        "thumbnail_text",
    ],
    "properties": {
        "title": {
            "type": "string",
            "description": "YouTube title, under 70 characters, no ALL CAPS, no emoji.",
        },
        "description": {
            "type": "string",
            "description": (
                "YouTube description: a 2-3 sentence summary, then a blank line, "
                "then 'Sources:' followed by 3-6 real, well-known reference works or "
                "institutions a viewer could check. No invented URLs."
            ),
        },
        "tags": {
            "type": "array",
            "description": "10-15 lowercase YouTube tags.",
            "items": {"type": "string"},
        },
        "thumbnail_prompt": {
            "type": "string",
            "description": "Image-generation prompt for a striking 16:9 thumbnail background. No text in the image.",
        },
        "thumbnail_text": {
            "type": "string",
            "description": "2-5 punchy words to overlay on the thumbnail.",
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "visual_prompt"],
                "properties": {
                    "narration": {
                        "type": "string",
                        "description": "Spoken narration for this scene. Plain prose, no stage directions, no markdown.",
                    },
                    "visual_prompt": {
                        "type": "string",
                        "description": (
                            "Self-contained image-generation prompt for this scene. "
                            "Describe a concrete scene, not an abstraction. No text or logos."
                        ),
                    },
                },
            },
        },
    },
}

SYSTEM = """You write narration for a factual YouTube documentary channel.

Hard rules:
- Every factual claim must be one you are confident is true and publicly verifiable.
  If you are unsure of a number or date, describe it qualitatively instead of inventing precision.
- Never invent quotes, studies, statistics, or URLs.
- Write narration as continuous spoken prose. No headings, no bullet points, no
  markdown, no stage directions, no speaker labels, no emoji.
- Never write "in this video", "welcome back", "don't forget to subscribe", or
  "let me know in the comments".
- Numbers must be written so a text-to-speech engine reads them correctly
  (write "nineteen forty-five", not "1945"; "twelve thousand", not "12,000").
- Each scene's visual_prompt must stand alone: an image model will see it with no
  other context. Describe a concrete, photographable scene.
- Do not describe living public figures, brand logos, or copyrighted characters in
  visual prompts.
"""


def plan(cfg: Config, topic: str) -> dict[str, Any]:
    target_seconds = int(cfg.get("script.target_seconds", 480))
    scene_seconds = max(6, int(cfg.get("script.scene_seconds", 14)))
    wpm = int(cfg.get("script.words_per_minute", 155))

    scene_count = max(4, round(target_seconds / scene_seconds))
    total_words = round(target_seconds / 60 * wpm)
    words_per_scene = max(20, round(total_words / scene_count))

    user = f"""Topic: {topic}

Channel: {cfg.get('channel.name')}
Niche: {cfg.get('channel.niche')}
Audience: {cfg.get('channel.audience')}
Language: {cfg.get('channel.language', 'English')}

Style: {cfg.get('script.style')}

Write exactly {scene_count} scenes.
Each scene's narration must be about {words_per_scene} words — roughly {scene_seconds} seconds spoken.
Total target: about {total_words} words, for a {target_seconds // 60}-minute video.

Structure:
- Scene 1 is the hook. Open a specific curiosity gap in the first two sentences.
  Do not summarise the answer.
- Middle scenes deliver the substance, one clear idea per scene, each ending in a
  small unresolved thread that pulls into the next.
- The final scene closes the loop opened in scene 1 and lands on a memorable
  final line.

Also produce the title, description, tags, and thumbnail fields.
"""

    data = complete_json(cfg, system=SYSTEM, user=user, schema=SCRIPT_SCHEMA, name="video_script")
    return _validate(data, topic)


def _validate(data: dict[str, Any], topic: str) -> dict[str, Any]:
    scenes = [s for s in data.get("scenes", []) if s.get("narration", "").strip()]
    if not scenes:
        raise ValueError("model returned no usable scenes")

    for i, scene in enumerate(scenes):
        scene["index"] = i
        scene["narration"] = " ".join(scene["narration"].split())
        if not scene.get("visual_prompt", "").strip():
            scene["visual_prompt"] = topic

    data["scenes"] = scenes
    data["topic"] = topic
    data["title"] = (data.get("title") or topic).strip()
    data["tags"] = [t.strip().lower() for t in data.get("tags", []) if t.strip()][:15]
    data["word_count"] = sum(len(s["narration"].split()) for s in scenes)
    return data


def estimate_seconds(data: dict[str, Any], wpm: int = 155) -> float:
    return data.get("word_count", 0) / wpm * 60


def estimate_cost_usd(cfg: Config, data: dict[str, Any]) -> dict[str, float]:
    """Rough per-video API cost, for the run summary. Not a billing source."""
    scenes = len(data.get("scenes", []))
    words = data.get("word_count", 0)
    chars = words * 6

    # gpt-4o-mini-tts: ~$0.60/1M input tokens, ~4 chars per token.
    tts = chars / 4 / 1_000_000 * 0.60
    # gpt-image-1 medium quality landscape, ~$0.04/image, plus the thumbnail.
    images = (scenes + 1) * 0.04 if cfg.get("visuals.source") == "ai" else 0.0
    # whisper-1: $0.006 per minute of audio.
    whisper = (
        (words / 155) * 0.006
        if cfg.get("captions.enabled") and cfg.get("captions.align") == "whisper"
        else 0.0
    )
    # Script call: a few thousand tokens in, a few thousand out.
    script = 0.05 if cfg.get("script.provider") == "anthropic" else 0.03

    return {
        "script": round(script, 3),
        "narration": round(tts, 3),
        "images": round(images, 3),
        "captions": round(whisper, 3),
        "total": round(script + tts + images + whisper, 2),
    }
