"""Anthropic client wrapper for the reasoning layer (spec §3).

Kept thin and optional: the system runs fully without an API key via the stub
reasoner in ``decide.py``. This wrapper is only used when LIVE reasoning is
requested and ANTHROPIC_API_KEY is present.
"""

from __future__ import annotations

import json
import os

from ..logging_setup import get_logger
from .prompts import FEWSHOT_EXEMPLAR, SYSTEM_PROMPT

_log = get_logger("reasoning.llm")


class AnthropicReasoner:
    """Calls Claude to produce a §8 decision JSON. Returns a dict or None."""

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def decide(self, context: dict) -> dict | None:
        if not self.available:
            _log.warning("no ANTHROPIC_API_KEY -> reasoner unavailable")
            return None
        try:
            import anthropic  # lazy import
        except ImportError:
            _log.error("anthropic package not installed")
            return None

        client = anthropic.Anthropic(api_key=self.api_key)
        user = (
            FEWSHOT_EXEMPLAR
            + "\n\nNOW DECIDE. Context (all numbers pre-computed by the engine):\n"
            + json.dumps(context, indent=2, default=str)
            + "\n\nReturn ONLY the §8 decision JSON in a fenced ```json block."
        )
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 — fail closed to PASS
            _log.error("LLM call failed: %s", exc)
            return None

        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _extract_json(text)


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of the model's reply."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
