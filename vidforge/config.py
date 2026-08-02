"""Paths, .env loading and config.yaml access.

No hardcoded absolute paths: everything hangs off PROJECT_ROOT, and the output
directory can be redirected with the VIDFORGE_OUTPUT_DIR env var.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
TOPICS_PATH = PROJECT_ROOT / "topics.txt"
MUSIC_DIR = PROJECT_ROOT / "assets" / "music"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
SECRETS_DIR = PROJECT_ROOT / ".secrets"

_env_loaded = False


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
