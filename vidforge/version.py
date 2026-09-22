"""The app's version, derived from the repository rather than hand-typed.

    v<MAJOR>.<BUILD>          e.g. v2.050

**MAJOR** is the product arc, the only hand-edited part: the `VERSION` file at
the project root, changed on a deliberate milestone.

**BUILD** is `git rev-list --count HEAD`, zero-padded to three digits. Derived,
so it cannot be forgotten — a hand-maintained build number is wrong the first
time someone ships without remembering to bump it, and then silently wrong
forever.

This is the lab-wide scheme, shared with `imprint`, `sonar` and `lab_hub`, and
the Lab Project Monitor computes the same string from the same two inputs, so
the dashboard and the running app cannot disagree.

A frozen `.app` has no `.git`, so the build is stamped into `_build_info.json`
at package time by `scripts/stamp_version.py` and read back here. A checkout
prefers live git, so an edit shows up on the next launch without re-stamping.
Where there is neither, this says `v2.???` rather than inventing a number:
claiming a version with no evidence is a lie told exactly when someone asks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_INFO_NAME = "_build_info.json"
BUILD_DIGITS = 3
FALLBACK_MAJOR = "2"
GIT_TIMEOUT_S = 5


def _resource_base() -> Path:
    """Where bundled files live: the unpacked bundle, or the checkout."""
    return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _read_major() -> str:
    for candidate in (PROJECT_ROOT / "VERSION", _resource_base() / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text.lstrip("vV").split(".")[0]
    return FALLBACK_MAJOR


def _git(*args: str) -> str | None:
    """Run git, or return None. Never raises: git may be absent and the
    directory may not be a repository, neither worth a traceback."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_build() -> dict | None:
    if not (PROJECT_ROOT / ".git").exists():
        return None
    count = _git("rev-list", "--count", "HEAD")
    if not count or not count.isdigit():
        return None
    return {
        "build": int(count),
        "commit": _git("rev-parse", "--short", "HEAD") or "",
        "date": _git("log", "-1", "--format=%cI") or "",
        "source": "git",
    }


def _baked() -> dict | None:
    for candidate in (
        _resource_base() / BUILD_INFO_NAME,
        PROJECT_ROOT / BUILD_INFO_NAME,
    ):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("build"), int):
            data.setdefault("source", "baked")
            return data
    return None


@lru_cache(maxsize=1)
def info() -> dict:
    """Everything known about the running build.

    Cached: it shells out to git and the answer cannot change while the process
    lives. Never call it from a repaint or a timer.
    """
    major = _read_major()
    # Frozen asks its stamp first: a bundle's own build is what it is running,
    # even when a checkout happens to sit beside it.
    found = (_baked() or _git_build()) if is_frozen() else (_git_build() or _baked())
    if not found:
        return {
            "major": major,
            "build": None,
            "version": f"v{major}.???",
            "commit": "",
            "date": "",
            "source": "unknown",
        }
    return {
        "major": major,
        "build": found["build"],
        "version": f"v{major}.{found['build']:0{BUILD_DIGITS}d}",
        "commit": found.get("commit", ""),
        "date": found.get("date", ""),
        "source": found.get("source", "unknown"),
    }


def version_string() -> str:
    """Just the badge text, e.g. `v2.050`."""
    return info()["version"]


def tooltip() -> str:
    """Where the number came from, for a hover."""
    found = info()
    if found["source"] == "unknown":
        return "No build information: not a checkout, and nothing stamped in."
    where = {
        "git": "read live from the checkout",
        "baked": "stamped in when this bundle was built",
    }.get(found["source"], found["source"])
    parts = [f"{found['version']} — {where}"]
    if found["commit"]:
        parts.append(f"commit {found['commit']}")
    if found["date"]:
        parts.append(found["date"][:10])
    return " · ".join(parts)
