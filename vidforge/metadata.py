"""YouTube-facing metadata: title, description, tags, chapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 4900  # YouTube's cap is 5000; leave room for chapters
TAG_TOTAL_LIMIT = 480     # YouTube's cap is 500 characters across all tags


def _timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


MIN_CHAPTER_GAP = 10.0  # YouTube requires chapters to be at least 10s apart


def chapters(plan: dict[str, Any], timings: list[dict[str, float]]) -> list[str]:
    """YouTube chapters: must start at 0:00, be >=10s apart, and number >=3."""
    scenes = plan.get("scenes", [])
    if len(scenes) < 3 or len(timings) < 3:
        return []

    step = max(1, len(scenes) // 6)  # aim for roughly six chapters
    lines: list[str] = []
    last_start = -MIN_CHAPTER_GAP

    for i in range(0, min(len(scenes), len(timings)), step):
        start = 0.0 if i == 0 else timings[i]["start"]
        if start - last_start < MIN_CHAPTER_GAP:
            continue
        label = " ".join(scenes[i]["narration"].split()[:6]).rstrip(".,;:—-")
        lines.append(f"{_timestamp(start)} {label}")
        last_start = start

    # A chapter list YouTube would reject is worse than none at all.
    return lines if len(lines) >= 3 and lines[0].startswith("0:00") else []


def build(
    plan: dict[str, Any], timings: list[dict[str, float]], *, duration: float
) -> dict[str, Any]:
    title = plan.get("title", plan.get("topic", "Untitled"))[:TITLE_LIMIT]

    body = plan.get("description", "").strip()
    chapter_lines = chapters(plan, timings)
    if chapter_lines:
        body = f"{body}\n\nChapters:\n" + "\n".join(chapter_lines)
    body = body[:DESCRIPTION_LIMIT]

    tags: list[str] = []
    used = 0
    for tag in plan.get("tags", []):
        if used + len(tag) + 1 > TAG_TOTAL_LIMIT:
            break
        tags.append(tag)
        used += len(tag) + 1

    return {
        "title": title,
        "description": body,
        "tags": tags,
        "topic": plan.get("topic"),
        "duration_seconds": round(duration, 2),
        "scene_count": len(plan.get("scenes", [])),
        "word_count": plan.get("word_count", 0),
        "chapters": chapter_lines,
    }


def write(meta: dict[str, Any], dst: Path) -> Path:
    dst.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return dst
