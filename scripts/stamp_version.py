#!/usr/bin/env python3
"""Write `_build_info.json` so a frozen bundle knows its own build.

A `.app` carries no `.git`, so the number has to be recorded at package time.
Run from `build_app.sh` before PyInstaller; the file is git-ignored because it
is build output, not source.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main() -> int:
    count = _git("rev-list", "--count", "HEAD")
    if not count.isdigit():
        print("stamp_version: not a git checkout; nothing stamped", file=sys.stderr)
        return 1
    major = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "2"
    payload = {
        "major": major.lstrip("vV").split(".")[0],
        "build": int(count),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "date": _git("log", "-1", "--format=%cI"),
        "source": "baked",
    }
    target = PROJECT_ROOT / "_build_info.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Stamped v{payload['major']}.{payload['build']:03d} into {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
