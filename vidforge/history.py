"""Record of what's been produced, so the pipeline never repeats a topic."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import output_root


def history_path() -> Path:
    return output_root() / "history.json"


def load() -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save(entries: list[dict[str, Any]]) -> None:
    history_path().write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record(entry: dict[str, Any]) -> None:
    """Insert or update an entry, keyed by slug."""
    entries = load()
    for i, existing in enumerate(entries):
        if existing.get("slug") == entry.get("slug"):
            entries[i] = {**existing, **entry}
            break
    else:
        entries.append(entry)
    save(entries)


def seen_topics() -> set[str]:
    out: set[str] = set()
    for entry in load():
        for field in ("topic", "title"):
            value = entry.get(field)
            if value:
                out.add(_normalise(value))
    return out


def is_new(topic: str) -> bool:
    return _normalise(topic) not in seen_topics()


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(text: str, *, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "video"


def new_slug(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{stamp}-{slugify(title)}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
