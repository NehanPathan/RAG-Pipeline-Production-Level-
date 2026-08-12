from __future__ import annotations

import json
from typing import Any


def parse_json_response(text: str) -> Any:
    """Best-effort JSON extraction from an LLM text completion.

    Handles markdown code fences and leading/trailing prose around the
    JSON payload. Returns {} on failure so callers can apply a safe
    fallback instead of propagating a parse error.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch) + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue

    return {}
