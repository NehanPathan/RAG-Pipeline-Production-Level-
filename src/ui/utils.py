from __future__ import annotations

import json
import os
from typing import Generator

import requests

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
_TIMEOUT = 10


def api_get(path: str, **kwargs) -> dict:
    resp = requests.get(f"{API_BASE}{path}", timeout=_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str, **kwargs) -> dict:
    resp = requests.post(f"{API_BASE}{path}", timeout=_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()


def api_delete(path: str) -> dict:
    resp = requests.delete(f"{API_BASE}{path}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def stream_chat(
    query: str,
    conversation_id: str | None = None,
) -> Generator[str, None, dict]:
    """
    Consumes the /api/v1/chat SSE stream and yields each token string.
    After the generator is exhausted, the final 'done' event payload is
    stored in the generator's return value (accessible via StopIteration.value
    when calling next() manually, or implicitly discarded by for-loops).

    Raises RuntimeError on API errors or upstream error events.
    """
    payload = {
        "query": query,
        "conversation_id": conversation_id,
        "stream": True,
    }

    final: dict = {}

    with requests.post(
        f"{API_BASE}/api/v1/chat",
        json=payload,
        stream=True,
        timeout=120,
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line: str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            if event["type"] == "token":
                yield event["content"]
            elif event["type"] == "done":
                final = event
                break
            elif event["type"] == "error":
                raise RuntimeError(event.get("message", "Unknown error from backend"))

    return final
