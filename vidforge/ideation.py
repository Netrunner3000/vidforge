"""Choosing what the next video is about."""

from __future__ import annotations

from pathlib import Path

from . import history
from .config import TOPICS_PATH, Config
from .llm import complete_json

IDEAS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ideas"],
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "why_it_works"],
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "A specific, concrete video topic phrased as a curiosity hook.",
                    },
                    "why_it_works": {
                        "type": "string",
                        "description": "One sentence on the curiosity gap it opens.",
                    },
                },
            },
        }
    },
}


def read_queue(path: Path | None = None) -> list[str]:
    path = path or TOPICS_PATH
    if not path.exists():
        return []
    topics = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            topics.append(line)
    return topics


def append_to_queue(topics: list[str], path: Path | None = None) -> int:
    path = path or TOPICS_PATH
    existing = {t.lower() for t in read_queue(path)}
    fresh = [t for t in topics if t.lower() not in existing]
    if not fresh:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        for topic in fresh:
            fh.write(topic + "\n")
    return len(fresh)


def suggest(cfg: Config, count: int = 10) -> list[dict[str, str]]:
    """Ask the model for fresh topic ideas that aren't already used or queued."""
    avoid = sorted(set(read_queue()) | {e.get("topic", "") for e in history.load()})
    avoid = [a for a in avoid if a][:80]

    system = (
        "You are a YouTube channel strategist. You propose video topics that earn "
        "clicks honestly: specific, verifiable, and genuinely interesting. You never "
        "propose clickbait you cannot substantiate, medical or financial advice, or "
        "topics that require footage you cannot legally use."
    )
    user = (
        f"Channel niche: {cfg.get('channel.niche')}\n"
        f"Audience: {cfg.get('channel.audience')}\n\n"
        f"Propose {count} new video topics.\n"
        "Each must be answerable from well-documented public facts, and must open a "
        "curiosity gap in the title itself.\n\n"
        "Do NOT repeat or closely paraphrase any of these already-used topics:\n"
        + "\n".join(f"- {a}" for a in avoid)
    )

    data = complete_json(cfg, system=system, user=user, schema=IDEAS_SCHEMA, name="ideas")
    return data.get("ideas", [])[:count]


def next_topic(cfg: Config, explicit: str | None = None) -> str:
    """Explicit topic > first unused queued topic > freshly generated idea."""
    if explicit:
        return explicit.strip()

    for topic in read_queue():
        if history.is_new(topic):
            return topic

    print("   topics.txt exhausted — generating fresh ideas")
    ideas = suggest(cfg, count=5)
    for idea in ideas:
        topic = idea.get("topic", "").strip()
        if topic and history.is_new(topic):
            append_to_queue([topic])
            return topic

    raise RuntimeError(
        "Could not find an unused topic. Add lines to topics.txt or run "
        "`main.py topics --suggest 10`."
    )
