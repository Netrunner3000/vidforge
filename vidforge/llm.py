"""Provider-agnostic JSON completion.

Two backends:
  openai     — default. Uses the Chat Completions strict json_schema response format.
  anthropic  — optional. Uses output_config.format on the Messages API.

Both return a parsed dict validated against the schema you pass in.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from .config import Config, optional_key, require_key

RETRIES = 3


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    """The model declined the request (Anthropic `stop_reason: refusal`)."""


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------

_openai_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=require_key("OPENAI_API_KEY", "the OpenAI backend"))
    return _openai_client


def _complete_openai(
    *, model: str, system: str, user: str, schema: dict[str, Any], name: str
) -> dict[str, Any]:
    response = _openai().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": True},
        },
    )
    content = response.choices[0].message.content
    if not content:
        raise LLMError("OpenAI returned an empty response")
    return json.loads(content)


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------

_anthropic_client = None


def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(
            api_key=require_key("ANTHROPIC_API_KEY", "the Anthropic backend")
        )
    return _anthropic_client


def _complete_anthropic(
    *, model: str, system: str, user: str, schema: dict[str, Any]
) -> dict[str, Any]:
    # Note: Claude Opus 5 rejects temperature/top_p, thinking is on by default,
    # and max_tokens covers thinking + response together — hence the headroom.
    response = _anthropic().messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMRefusal(
            f"Claude declined this request (category={category!r}). "
            "Rephrase the topic, or switch script.provider to openai."
        )
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude hit max_tokens before finishing the JSON payload")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise LLMError("Anthropic returned no text block")
    return json.loads(text)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def complete_json(
    cfg: Config,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    name: str = "result",
) -> dict[str, Any]:
    """Ask the configured provider for a JSON object matching `schema`."""
    provider = str(cfg.get("script.provider", "openai")).lower()

    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            if provider == "anthropic":
                return _complete_anthropic(
                    model=cfg.get("script.anthropic_model", "claude-opus-5"),
                    system=system,
                    user=user,
                    schema=schema,
                )
            if provider == "openai":
                return _complete_openai(
                    model=cfg.get("script.openai_model", "gpt-4o"),
                    system=system,
                    user=user,
                    schema=schema,
                    name=name,
                )
            raise LLMError(
                f"unknown script.provider {provider!r} (expected 'openai' or 'anthropic')"
            )
        except LLMRefusal:
            raise  # retrying an identical refused prompt is pointless
        except Exception as exc:  # noqa: BLE001 - surfaced after retries
            last = exc
            if attempt < RETRIES:
                wait = 2 * attempt + random.random()
                print(f"   LLM attempt {attempt}/{RETRIES} failed ({exc}); retrying in {wait:.1f}s")
                time.sleep(wait)

    raise LLMError(f"LLM call failed after {RETRIES} attempts: {last}")


def provider_available(cfg: Config) -> tuple[bool, str]:
    """(ok, message) — whether the configured script provider has a usable key."""
    provider = str(cfg.get("script.provider", "openai")).lower()
    key = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    if optional_key(key):
        return True, f"{provider} ({key} present)"
    return False, f"{provider} backend selected but {key} is not set"
