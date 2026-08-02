"""One still image per scene.

Three sources, in descending order of cost and quality:
  ai       — gpt-image-1 from the scene's visual_prompt
  pexels   — stock photo search (needs PEXELS_API_KEY)
  gradient — procedurally drawn card, no network, no cost

Any source falls back to `gradient` for a single scene rather than failing the
whole render.
"""

from __future__ import annotations

import base64
import colorsys
import hashlib
import random
import time
from pathlib import Path
from typing import Any

from .config import Config, optional_key, require_key
from .progress import Reporter

RETRIES = 3
_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=require_key("OPENAI_API_KEY", "image generation"))
    return _client


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def _generate_ai(cfg: Config, prompt: str, dst: Path) -> None:
    suffix = " ".join(str(cfg.get("visuals.style_suffix", "")).split())
    full = f"{prompt}. {suffix}".strip()

    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            result = _openai().images.generate(
                model=cfg.get("visuals.image_model", "gpt-image-1"),
                prompt=full,
                size=cfg.get("visuals.image_size", "1536x1024"),
                quality=cfg.get("visuals.image_quality", "medium"),
                n=1,
            )
            payload = result.data[0]
            if getattr(payload, "b64_json", None):
                dst.write_bytes(base64.b64decode(payload.b64_json))
                return
            if getattr(payload, "url", None):
                import requests

                resp = requests.get(payload.url, timeout=60)
                resp.raise_for_status()
                dst.write_bytes(resp.content)
                return
            raise RuntimeError("image response contained neither b64_json nor url")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES:
                time.sleep(2 * attempt + random.random())

    raise RuntimeError(f"image generation failed: {last}")


def _generate_pexels(cfg: Config, prompt: str, dst: Path) -> None:
    import requests

    key = optional_key("PEXELS_API_KEY")
    if not key:
        raise RuntimeError("visuals.source is 'pexels' but PEXELS_API_KEY is not set")

    query = " ".join(prompt.split()[:6])
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": key},
        params={
            "query": query,
            "per_page": 5,
            "orientation": cfg.get("visuals.pexels_orientation", "landscape"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"no Pexels results for {query!r}")

    src = random.choice(photos)["src"]
    url = src.get("original") or src.get("large2x") or src.get("large")
    image = requests.get(url, timeout=60)
    image.raise_for_status()
    dst.write_bytes(image.content)


def _generate_gradient(cfg: Config, prompt: str, dst: Path) -> None:
    """Deterministic abstract card — the always-works fallback."""
    from PIL import Image, ImageDraw, ImageFilter

    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))

    seed = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    hue = rng.random()

    def rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        return int(r * 255), int(g * 255), int(b * 255)

    top = rgb(hue, 0.55, 0.30)
    bottom = rgb(hue + 0.08, 0.70, 0.08)

    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(height - 1, 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )

    for _ in range(rng.randint(3, 6)):
        cx, cy = rng.randint(0, width), rng.randint(0, height)
        radius = rng.randint(width // 8, width // 3)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=rgb(hue + rng.uniform(-0.15, 0.15), 0.45, rng.uniform(0.25, 0.5)),
        )

    image.filter(ImageFilter.GaussianBlur(radius=width // 22)).save(dst, "PNG")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def render_scenes(
    cfg: Config,
    scenes: list[dict[str, Any]],
    image_dir: Path,
    reporter: Reporter | None = None,
) -> list[Path]:
    """Produce (or reuse) one image per scene."""
    reporter = reporter or Reporter()
    image_dir.mkdir(parents=True, exist_ok=True)
    source = str(cfg.get("visuals.source", "ai")).lower()
    paths: list[Path] = []
    total = len(scenes)

    for scene in scenes:
        reporter.substep(scene["index"], total, f"scene {scene['index'] + 1}/{total}")
        dst = image_dir / f"scene_{scene['index']:03d}.png"
        if dst.exists() and dst.stat().st_size > 1024:
            reporter.log(f"scene {scene['index'] + 1}/{total} image cached")
            paths.append(dst)
            continue

        reporter.log(f"scene {scene['index'] + 1}/{total} illustrating")
        try:
            generate(cfg, source, scene["visual_prompt"], dst)
        except Exception as exc:  # noqa: BLE001 - degrade one scene, not the run
            reporter.log(f"! {source} failed for scene {scene['index'] + 1}: {exc}")
            reporter.log("  falling back to a gradient card for this scene")
            _generate_gradient(cfg, scene["visual_prompt"], dst)

        paths.append(dst)

    return paths


def generate(cfg: Config, source: str, prompt: str, dst: Path) -> None:
    if source == "ai":
        _generate_ai(cfg, prompt, dst)
    elif source == "pexels":
        _generate_pexels(cfg, prompt, dst)
    elif source == "gradient":
        _generate_gradient(cfg, prompt, dst)
    else:
        raise ValueError(f"unknown visuals.source {source!r}")
