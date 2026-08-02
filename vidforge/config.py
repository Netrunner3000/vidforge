"""Paths, .env loading and config.yaml access.

No hardcoded absolute paths: everything hangs off PROJECT_ROOT, and the output
directory can be redirected with the VIDFORGE_OUTPUT_DIR env var.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


# BUNDLE_ROOT holds read-only resources shipped with the code.
# PROJECT_ROOT is the writable home for config, topics, secrets and output.
# In a .app bundle those differ: the bundle is read-only and gets thrown away on
# every rebuild, so user data lives in Application Support instead.
if _frozen():
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    PROJECT_ROOT = Path.home() / "Library" / "Application Support" / "vidforge"
else:
    BUNDLE_ROOT = PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
TOPICS_PATH = PROJECT_ROOT / "topics.txt"
MUSIC_DIR = PROJECT_ROOT / "assets" / "music"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
SECRETS_DIR = PROJECT_ROOT / ".secrets"

_env_loaded = False

# Files seeded into PROJECT_ROOT on first launch of a bundled build.
_SEED_FILES = ("config.yaml", "topics.txt")


def ensure_user_root() -> Path:
    """Create the writable data dir and seed defaults. No-op when not frozen."""
    if not _frozen():
        return PROJECT_ROOT

    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "music").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "fonts").mkdir(parents=True, exist_ok=True)

    for name in _SEED_FILES:
        target = PROJECT_ROOT / name
        source = BUNDLE_ROOT / name
        if not target.exists() and source.exists():
            shutil.copy2(source, target)

    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        # Seed with empty values, not the placeholders from .env.example — a
        # literal "sk-..." would read as a configured key everywhere we check.
        env_file.write_text(
            "# vidforge keys. Paste your own values after the = signs.\n"
            "# Required: script writing, narration, images, caption alignment.\n"
            "OPENAI_API_KEY=\n\n"
            "# Optional: only if script.provider is set to anthropic.\n"
            "ANTHROPIC_API_KEY=\n\n"
            "# Optional: only if visuals.source is set to pexels.\n"
            "PEXELS_API_KEY=\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)

    return PROJECT_ROOT


def load_env() -> None:
    """Load .env from the project root, then the cwd. Never overrides real env vars."""
    global _env_loaded
    if _env_loaded:
        return
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    load_dotenv(override=False)
    _env_loaded = True


def output_root() -> Path:
    load_env()
    raw = os.getenv("VIDFORGE_OUTPUT_DIR", "").strip()
    root = Path(raw).expanduser() if raw else PROJECT_ROOT / "output"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Config:
    """Dotted-path read access over config.yaml, with CLI overrides layered on top."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"config not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(data)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Apply {dotted.key: value} pairs, skipping Nones (unset CLI flags)."""
        for key, value in overrides.items():
            if value is not None:
                self.set(key, value)

    def as_dict(self) -> dict[str, Any]:
        return self._data


def require_key(name: str, why: str) -> str:
    load_env()
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set — needed for {why}.\n"
            f"Add it to {PROJECT_ROOT / '.env'} (see .env.example)."
        )
    return value


def optional_key(name: str) -> str | None:
    load_env()
    value = os.getenv(name, "").strip()
    return value or None
