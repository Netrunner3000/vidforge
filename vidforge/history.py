"""Record of what's been produced, so the pipeline never repeats a topic."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
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
        # Never treat a corrupt index as an empty library: returning [] here
        # meant the next save() overwrote the file and wiped every entry.
        # Quarantine it for recovery and start fresh alongside.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        try:
            path.rename(path.with_name(f"history.corrupt-{stamp}.json"))
        except OSError:
            pass
        return []
    return data if isinstance(data, list) else []


def save(entries: list[dict[str, Any]]) -> None:
    # Atomic replace: two front doors (vidforge.app and Imprint) plus the
    # nightly launchd job share this file, and a truncating write_text
    # interrupted midway is how a library index disappears.
    path = history_path()
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".history-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(entry: dict[str, Any]) -> None:
    """Insert or update an entry, keyed by slug."""
    # Cross-process lock around the read-modify-write: without it, two
    # writers interleaving load() and save() lose whichever entry landed
    # first (last-writer-wins on the whole file). flock is advisory, but
    # every writer goes through this function.
    lock_path = history_path().with_name("history.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            entries = load()
            for i, existing in enumerate(entries):
                if existing.get("slug") == entry.get("slug"):
                    entries[i] = {**existing, **entry}
                    break
            else:
                entries.append(entry)
            save(entries)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
