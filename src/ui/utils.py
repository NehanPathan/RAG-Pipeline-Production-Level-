from __future__ import annotations

import json
import os
from collections.abc import Generator

import requests

from src.ui.auth import auth_headers

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
_TIMEOUT = 15

# Every request in the UI goes through these helpers rather than calling
# requests/httpx directly. That is what makes it impossible to add a page that
# silently talks to the API unauthenticated -- the previous shape (each page
# building its own httpx call) had no such guarantee, and a missed header
# would surface as a 401 that looks like a login bug.


class APIError(RuntimeError):
    """A backend call failed in a way worth showing the user."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_auth_error(self) -> bool:
        return self.status_code in (401, 403)


def _headers(extra: dict | None = None) -> dict:
    headers = auth_headers()
    if extra:
        headers.update(extra)
    return headers


def _handle(response: requests.Response) -> dict:
    if response.status_code == 401:
        raise APIError("Your session is not valid. Sign in again.", 401)
    if response.status_code == 403:
        # The most common cause is clearance, and the message the backend
        # sends explains which role is required -- surfacing it beats a
        # generic "forbidden".
        detail = _detail(response) or "You do not have permission for that."
        raise APIError(detail, 403)
    if response.status_code == 404:
        raise APIError(_detail(response) or "Not found.", 404)
    if response.status_code >= 400:
        raise APIError(
            _detail(response) or f"Request failed ({response.status_code}).",
            response.status_code,
        )
    if not response.content:
        return {}
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise APIError("The API returned a non-JSON response.") from exc


def _detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", d)) for d in detail)
    return str(detail) if detail else ""


def api_get(path: str, params: dict | None = None, timeout: int = _TIMEOUT) -> dict:
    return _handle(
        requests.get(f"{API_BASE}{path}", params=params, headers=_headers(), timeout=timeout)
    )


def api_post(
    path: str,
    json_body: dict | None = None,
    params: dict | None = None,
    files: dict | None = None,
    data: dict | None = None,
    timeout: int = 120,
) -> dict:
    return _handle(
        requests.post(
            f"{API_BASE}{path}",
            json=json_body,
            params=params,
            files=files,
            data=data,
            headers=_headers(),
            timeout=timeout,
        )
    )


def api_put(path: str, json_body: dict | None = None, timeout: int = _TIMEOUT) -> dict:
    return _handle(
        requests.put(f"{API_BASE}{path}", json=json_body, headers=_headers(), timeout=timeout)
    )


def api_delete(path: str, timeout: int = _TIMEOUT) -> dict:
    return _handle(requests.delete(f"{API_BASE}{path}", headers=_headers(), timeout=timeout))


def stream_chat(
    query: str,
    conversation_id: str | None = None,
) -> Generator[str, None, dict]:
    """Consume the /api/v1/chat SSE stream, yielding each token.

    The final `done` event is the generator's return value, so a caller that
    needs the citations, route or trace id reads it from `StopIteration.value`
    (or via `yield from` inside another generator).

    Raises APIError on transport/auth failures and RuntimeError on an upstream
    error event.
    """
    payload = {"query": query, "conversation_id": conversation_id, "stream": True}
    final: dict = {}

    with requests.post(
        f"{API_BASE}/api/v1/chat",
        json=payload,
        stream=True,
        headers=_headers(),
        timeout=180,
    ) as response:
        if response.status_code >= 400:
            # Drain before raising: the body carries the reason, and a
            # streaming response left open holds the connection.
            raise APIError(
                _detail(response) or f"Chat request failed ({response.status_code}).",
                response.status_code,
            )

        for raw in response.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
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
